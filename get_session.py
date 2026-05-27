from playwright.sync_api import sync_playwright

def login_and_save():
    with sync_playwright() as p:
        # headless=False means you will actually see the browser pop up
        browser = p.chromium.launch(headless=False) 
        context = browser.new_context()
        page = context.new_page()
        
        print("Opening VLE... Please log in and approve the Authenticator prompt.")
        page.goto("https://vle.unikl.edu.my/auth/oidc/")
        
        # The script will wait here until you successfully finish MFA 
        # and land on the dashboard. You have 5 minutes to do it.
        page.wait_for_url("https://vle.unikl.edu.my/my/", timeout=300000)
        
        print("MFA Cleared! Saving session state...")
        
        # This saves your cookies, bypassing future MFA checks
        context.storage_state(path="storageState.json")
        print("Saved to storageState.json. You can close the browser.")
        browser.close()

if __name__ == "__main__":
    login_and_save()
