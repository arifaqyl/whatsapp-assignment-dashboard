from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import config as app_config
import db as ops_db
from paths import SESSION_FILE
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

VLE_BASE_URL = getattr(app_config, "VLE_BASE_URL", "https://vle.example.edu.my").rstrip("/")
VLE_EMAIL = getattr(app_config, "VLE_EMAIL", "")
VLE_PASSWORD = getattr(app_config, "VLE_PASSWORD", "")


def _set_login_state(login_state, status, message, *, health_status=None):
    login_state["status"] = status
    login_state["message"] = message
    if health_status is None:
        health_status = "error" if status == "error" else status
    try:
        ops_db.record_system_health("vle_login", health_status, f"{status}: {message}")
    except Exception:
        pass


def _first_visible(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                return locator
        except Exception:
            continue
    return None


def _click_if_visible(page, selectors):
    locator = _first_visible(page, selectors)
    if not locator:
        return False
    try:
        locator.click(timeout=2000)
        return True
    except Exception:
        return False


def _fill_if_visible(page, selectors, value):
    locator = _first_visible(page, selectors)
    if not locator:
        return False
    try:
        locator.fill(value, timeout=2000)
        return True
    except Exception:
        return False


def _has_visible_text(page, patterns):
    for pattern in patterns:
        try:
            locator = page.locator(f"text=/{pattern}/i").first
            if locator.count() and locator.is_visible():
                return True
        except Exception:
            continue
    return False


def _is_number_match_prompt(page):
    return _has_visible_text(
        page,
        [
            r"enter the number shown",
            r"number shown to sign in",
            r"approve sign in request",
            r"open your authenticator app",
            r"use your microsoft authenticator app",
            r"check your authenticator app",
        ],
    )


def _is_otp_prompt(page):
    otp_box = _first_visible(page, [
        'input[name="otc"]',
        'input[name="code"]',
        'input#idTxtBx_SAOTCC_OTC',
    ])
    if otp_box:
        return True
    if _has_visible_text(
        page,
        [
            r"enter code",
            r"verification code",
            r"one.time code",
            r"otp code",
            r"authenticator code",
        ],
    ):
        return True
    return False


def _await_mfa_code(login_state, timeout_seconds=180):
    _set_login_state(login_state, "waiting_code", "Waiting for /code 123456")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        code = (login_state.get("code") or "").strip()
        if code:
            login_state["code"] = None
            return code
        time.sleep(1)
    raise TimeoutError("Timed out waiting for MFA code")


def _handle_microsoft_flow(page, login_state):
    deadline = time.time() + 300
    email_sent = False
    password_sent = False

    while time.time() < deadline:
        url = page.url
        if url.startswith(f"{VLE_BASE_URL}/my/"):
            return

        # Email / identifier page
        email_box = _first_visible(page, [
            'input[type="email"]',
            'input[name="loginfmt"]',
            'input#i0116',
        ])
        if email_box and VLE_EMAIL and not email_sent:
            email_box.fill(VLE_EMAIL, timeout=3000)
            _click_if_visible(page, ['input[type="submit"]', 'button[type="submit"]', '#idSIButton9'])
            email_sent = True
            _set_login_state(login_state, "submitting_email", "Submitting Microsoft email")
            page.wait_for_timeout(1000)
            continue

        # Password page
        password_box = _first_visible(page, [
            'input[type="password"]',
            'input[name="passwd"]',
            'input#i0118',
        ])
        if password_box and VLE_PASSWORD and not password_sent:
            password_box.fill(VLE_PASSWORD, timeout=3000)
            _click_if_visible(page, ['input[type="submit"]', 'button[type="submit"]', '#idSIButton9'])
            password_sent = True
            _set_login_state(login_state, "submitting_password", "Submitting Microsoft password")
            page.wait_for_timeout(1500)
            continue

        # Authenticator approval / code selection
        if _click_if_visible(page, [
            'text=/use a verification code/i',
            'text=/use a code/i',
            'text=/sign in another way/i',
            'text=/i can.t use my microsoft authenticator app right now/i',
        ]):
            _set_login_state(login_state, "choosing_mfa_method", "Choosing alternate MFA method")
            page.wait_for_timeout(1500)
            continue

        # Number match / approval prompt must win before generic numeric-input checks.
        if _is_number_match_prompt(page):
            _set_login_state(login_state, "waiting_approval", "Approve the sign-in on your phone. If Microsoft shows a number, enter that number in Authenticator.")
            _click_if_visible(page, ['#idSIButton9', 'text=/yes/i', 'text=/continue/i'])
            page.wait_for_timeout(1500)
            continue

        # OTP code page
        otp_box = _first_visible(page, [
            'input[name="otc"]',
            'input[name="code"]',
            'input#idTxtBx_SAOTCC_OTC',
        ])
        if otp_box or _is_otp_prompt(page):
            if not otp_box:
                otp_box = _first_visible(page, [
                    'input[name="otc"]',
                    'input[name="code"]',
                    'input#idTxtBx_SAOTCC_OTC',
                ])
            if not otp_box:
                page.wait_for_timeout(1000)
                continue
            code = _await_mfa_code(login_state)
            otp_box.fill(code, timeout=3000)
            _click_if_visible(page, ['input[type="submit"]', 'button[type="submit"]', '#idSubmit_SAOTCC_Continue', '#idSIButton9'])
            _set_login_state(login_state, "submitting_code", "Submitting MFA code")
            page.wait_for_timeout(1500)
            continue

        # Approval prompt / generic Microsoft page
        if "microsoftonline.com" in url or "login.live.com" in url:
            _set_login_state(login_state, "waiting_approval", "Approve the sign-in on your phone. If Microsoft shows a number, enter that number in Authenticator.")
            _click_if_visible(page, ['#idSIButton9', 'text=/yes/i', 'text=/continue/i'])
            page.wait_for_timeout(1500)
            continue

        page.wait_for_timeout(1000)

    raise TimeoutError("Timed out during Microsoft sign-in flow")


def login_and_save(login_state=None):
    login_state = login_state or {}
    if not VLE_EMAIL or not VLE_PASSWORD:
        raise RuntimeError("VLE_EMAIL or VLE_PASSWORD is missing in config.py")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context()
        page = context.new_page()

        _set_login_state(login_state, "opening", "Opening VLE login flow")
        page.goto(f"{VLE_BASE_URL}/auth/oidc/", timeout=60000)

        try:
            page.wait_for_url(f"{VLE_BASE_URL}/my/", timeout=10000)
        except PlaywrightTimeoutError:
            _handle_microsoft_flow(page, login_state)
            page.wait_for_url(f"{VLE_BASE_URL}/my/", timeout=120000)

        _set_login_state(login_state, "saving", "Saving refreshed VLE session")
        context.storage_state(path=str(SESSION_FILE))
        browser.close()
        _set_login_state(login_state, "done", "VLE session refreshed", health_status="ok")


def probe_login_flow(max_seconds=20):
    result = {
        "status": "unknown",
        "detail": "",
        "final_url": None,
    }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context()
            page = context.new_page()
            page.goto(f"{VLE_BASE_URL}/auth/oidc/", timeout=60000)

            deadline = time.time() + max_seconds
            while time.time() < deadline:
                url = page.url
                result["final_url"] = url

                if url.startswith(f"{VLE_BASE_URL}/my/"):
                    result["status"] = "vle_ready"
                    result["detail"] = "flow reached /my/"
                    browser.close()
                    return result

                if _first_visible(page, ['input[type="email"]', 'input[name="loginfmt"]', 'input#i0116']):
                    result["status"] = "needs_email"
                    result["detail"] = "Microsoft email screen is visible"
                    browser.close()
                    return result

                if _first_visible(page, ['input[type="password"]', 'input[name="passwd"]', 'input#i0118']):
                    result["status"] = "needs_password"
                    result["detail"] = "Microsoft password screen is visible"
                    browser.close()
                    return result

                if _is_number_match_prompt(page):
                    result["status"] = "needs_approval"
                    result["detail"] = "Microsoft approval or number-match prompt is visible"
                    browser.close()
                    return result

                if _is_otp_prompt(page):
                    result["status"] = "needs_code"
                    result["detail"] = "OTP code entry screen is visible"
                    browser.close()
                    return result

                if "microsoftonline.com" in url or "login.live.com" in url:
                    result["status"] = "needs_approval"
                    result["detail"] = "Microsoft auth page is active; likely waiting for approval or next factor"
                    browser.close()
                    return result

                if "login/index.php" in url:
                    result["status"] = "moodle_login"
                    result["detail"] = "Moodle login page is active"
                    browser.close()
                    return result

                page.wait_for_timeout(1000)

            browser.close()
    except Exception as exc:
        result["status"] = "probe_error"
        result["detail"] = str(exc)
        return result

    result["detail"] = "login preview timed out without a clear state"
    return result


def probe_saved_session():
    session_path = str(SESSION_FILE)
    result = {
        "exists": os.path.exists(session_path),
        "path": session_path,
        "age_minutes": None,
        "valid": False,
        "final_url": None,
        "status": "missing",
        "detail": "",
    }
    if not result["exists"]:
        result["detail"] = "storageState.json missing"
        return result

    try:
        mtime = os.path.getmtime(session_path)
        result["age_minutes"] = int((time.time() - mtime) // 60)
    except OSError:
        pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(storage_state=session_path)
            page = context.new_page()
            page.goto(f"{VLE_BASE_URL}/my/", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            final_url = page.url
            browser.close()
    except Exception as exc:
        result["status"] = "probe_error"
        result["detail"] = str(exc)
        return result

    result["final_url"] = final_url
    if final_url.startswith(f"{VLE_BASE_URL}/my/"):
        result["valid"] = True
        result["status"] = "valid"
        result["detail"] = "saved session reaches /my/"
    elif "login/index.php" in final_url or "microsoftonline.com" in final_url:
        result["status"] = "expired"
        result["detail"] = "saved session redirects to login"
    else:
        result["status"] = "unknown"
        result["detail"] = f"unexpected landing url: {final_url}"
    return result


if __name__ == "__main__":
    state = {"status": "standalone", "code": None}
    login_and_save(state)
    print(f"Saved to {SESSION_FILE}")
