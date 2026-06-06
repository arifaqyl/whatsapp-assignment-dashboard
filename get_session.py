import config as app_config
from playwright.sync_api import sync_playwright

VLE_BASE_URL = getattr(app_config, "VLE_BASE_URL", "https://vle.example.edu.my").rstrip("/")

def login_and_save():
    with sync_playwright() as p:
        # headless=False means you will actually see the browser pop up
        browser = p.chromium.launch(headless=False) 
        context = browser.new_context()
        page = context.new_page()
        
        print("Opening VLE... Please log in and approve the Authenticator prompt.")
        page.goto(f"{VLE_BASE_URL}/auth/oidc/")
        
        # The script will wait here until you successfully finish MFA 
        # and land on the dashboard. You have 5 minutes to do it.
        page.wait_for_url(f"{VLE_BASE_URL}/my/", timeout=300000)
        
        print("MFA Cleared! Saving session state...")
        
        # This saves your cookies, bypassing future MFA checks
        context.storage_state(path="storageState.json")
        print("Saved to storageState.json. You can close the browser.")
        browser.close()

if __name__ == "__main__":
    login_and_save()
