#!/usr/bin/env python3
"""
Falabella FACL Staging - Order Creation Automation

Option A - Use your existing Chrome session (recommended):
    1. Quit Chrome completely (Cmd+Q)
    2. Relaunch Chrome with debugging enabled:
       /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222
    3. In that Chrome window, log into staging.falabella.com normally
    4. Run the script:
       python3 create_order.py 7144554 --connect

Option B - Playwright login + session save:
    python3 create_order.py --login          # Opens browser for manual Cloudflare + Falabella login
    python3 create_order.py 7144554          # Headless (after login session saved)

Note: Must run from a machine with access to staging.falabella.com (corporate VPN).
"""

import sys
import re
import time
import json
import os
import argparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# --- Configuration ---
BASE_URL = "https://staging.falabella.com"
LOGIN_URL = f"{BASE_URL}/falabella-cl/login"
CART_URL = f"{BASE_URL}/falabella-cl/basket"
ORDERS_URL = f"{BASE_URL}/falabella-cl/orders"

# Test account (FACL staging - KeyCloak migrated, mock enabled)
EMAIL = "clstgall001@yopmail.com"
PASSWORD = "Test@123"

# Timeouts (ms)
NAV_TIMEOUT = 60000
ACTION_TIMEOUT = 30000
LONG_TIMEOUT = 120000

# State file for cookies/session persistence
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_state.json")


def parse_args():
    parser = argparse.ArgumentParser(description="Create a test order on Falabella FACL staging")
    parser.add_argument("sku_or_url", nargs="?", default=None,
                        help="Product SKU (e.g. 7144554) or full product URL")
    parser.add_argument("--login", action="store_true",
                        help="Interactive login mode: opens browser for manual Cloudflare + Falabella auth, saves session")
    parser.add_argument("--connect", action="store_true",
                        help="Connect to your existing Chrome session (must launch Chrome with --remote-debugging-port=9222)")
    parser.add_argument("--debug-port", type=int, default=9222,
                        help="Chrome remote debugging port (default: 9222)")
    parser.add_argument("--headed", action="store_true",
                        help="Run with visible browser (recommended for first run)")
    parser.add_argument("--slow", type=int, default=0,
                        help="Slow down actions by N ms (useful for debugging)")
    parser.add_argument("--clear-cart", action="store_true",
                        help="Clear cart before adding new product")
    parser.add_argument("--account", type=str, default=EMAIL,
                        help="Override test account email")
    parser.add_argument("--password", type=str, default=PASSWORD,
                        help="Override test account password")
    args = parser.parse_args()
    if not args.login and not args.sku_or_url:
        parser.error("Please provide a SKU/URL, or use --login to set up authentication first")
    return args


def sku_to_url(sku_or_url):
    """Convert a SKU to a product URL, or return URL as-is."""
    if sku_or_url.startswith("http"):
        return sku_or_url
    sku = sku_or_url.strip()
    return f"{BASE_URL}/falabella-cl/product/{sku}"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def close_popups(page):
    """Close any popups, modals, or cookie banners."""
    popup_selectors = [
        'button:has-text("Aceptar")',
        'button:has-text("Continuar compra")',
        'button[aria-label="close"]',
        'button[aria-label="Close"]',
        '[class*="modal"] [class*="close"]',
        '[class*="cookie"] button',
    ]
    for selector in popup_selectors:
        try:
            els = page.query_selector_all(selector)
            for el in els:
                if el.is_visible():
                    el.click()
                    time.sleep(0.3)
        except Exception:
            pass


def save_screenshot(page, name):
    """Save a debug screenshot."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.png")
    page.screenshot(path=path)
    log(f"  Screenshot saved: {name}.png")


def interactive_login(context, page):
    """Interactive login: user manually completes Cloudflare Access + Falabella login."""
    log("=== INTERACTIVE LOGIN MODE ===")
    log("")
    log("A browser window will open. Please complete these steps:")
    log("  1. Complete the Cloudflare Access authentication (Azure AD or email code)")
    log("  2. Once on the Falabella site, log in with your test account")
    log("     (e.g. clstgall001@yopmail.com / Test@123)")
    log("  3. Wait until you see the Falabella homepage with 'Hola' greeting")
    log("  4. Come back here and press ENTER to save the session")
    log("")

    page.goto(BASE_URL + "/falabella-cl", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

    input(">>> Press ENTER here once you're logged in on the Falabella site... ")

    # Save the auth state (Cloudflare + Falabella cookies)
    context.storage_state(path=STATE_FILE)
    log("")
    log("  Session saved! You can now run orders without manual login:")
    log("  python3 create_order.py 7144554")
    log("  python3 create_order.py 7144554 --headed")
    log("")
    log("  The session will stay valid for a while. If you get auth errors")
    log("  later, just run --login again to refresh it.")
    return True


def login(page, email, password):
    """Check if already authenticated, handle Cloudflare Access + Falabella login."""
    log("Step 1: Checking authentication...")

    # Try to go to the Falabella site
    page.goto(BASE_URL + "/falabella-cl", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    time.sleep(3)

    content = page.content()

    # Check for Cloudflare Access gate
    if "cloudflareaccess" in page.url or "cloudflareaccess" in content.lower():
        raise Exception(
            "Cloudflare Access authentication required!\n"
            "Run this first to set up your session:\n"
            "  python3 create_order.py --login\n"
            "This opens a browser for you to manually authenticate once.\n"
            "After that, automated runs will use the saved session."
        )

    # Check for WAF block
    if "bloqueado" in content.lower():
        raise Exception(
            "Access blocked by WAF. Try:\n"
            "  1. Connect to corporate VPN\n"
            "  2. Run: python3 create_order.py --login"
        )

    # Check if already logged into Falabella
    if "Hola," in content and "Inicia sesión" not in content:
        log("  Already logged in!")
        return True

    # Past Cloudflare but not logged into Falabella - do automated Falabella login
    log("  Cloudflare OK, logging into Falabella...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    time.sleep(3)

    content = page.content()

    # If Cloudflare gate appears on login page too, need --login
    if "cloudflareaccess" in page.url or "cloudflareaccess" in content.lower():
        raise Exception(
            "Cloudflare Access authentication required!\n"
            "Run: python3 create_order.py --login"
        )

    # Find and fill email
    email_selectors = [
        'input[type="email"]',
        'input[name="email"]',
        'input[id="email"]',
        'input[name="username"]',
        'input[id="username"]',
        'input[placeholder*="correo"]',
        'input[placeholder*="email"]',
        'input[placeholder*="Email"]',
        '#loginForm input[type="text"]',
    ]

    email_input = None
    for selector in email_selectors:
        try:
            el = page.wait_for_selector(selector, state="visible", timeout=5000)
            if el:
                email_input = el
                break
        except PlaywrightTimeout:
            continue

    if not email_input:
        save_screenshot(page, "login_page_debug")
        raise Exception(
            "Could not find email input on login page. "
            "Screenshot saved as login_page_debug.png"
        )

    email_input.fill(email)
    log(f"  Entered email: {email}")
    time.sleep(1)

    # Check for two-step login (email first, then password)
    continue_btn = page.query_selector(
        'button:has-text("Continuar"), button:has-text("Siguiente"), button:has-text("Next")'
    )
    if continue_btn and continue_btn.is_visible():
        continue_btn.click()
        log("  Clicked continue (two-step login)")
        time.sleep(2)

    # Fill password
    pwd_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[id="password"]',
    ]

    pwd_input = None
    for selector in pwd_selectors:
        try:
            el = page.wait_for_selector(selector, state="visible", timeout=5000)
            if el:
                pwd_input = el
                break
        except PlaywrightTimeout:
            continue

    if not pwd_input:
        save_screenshot(page, "password_page_debug")
        raise Exception("Could not find password input. Check password_page_debug.png")

    pwd_input.fill(password)
    log("  Entered password")
    time.sleep(1)

    # Click login/submit button
    submit_selectors = [
        'button[type="submit"]',
        'button:has-text("Iniciar sesión")',
        'button:has-text("Iniciar Sesión")',
        'button:has-text("Ingresar")',
        'button:has-text("Login")',
        'button:has-text("Sign in")',
        'input[type="submit"]',
    ]

    for selector in submit_selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                log("  Clicked login button")
                break
        except Exception:
            continue

    try:
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
    except PlaywrightTimeout:
        pass
    time.sleep(3)

    # Save updated auth state
    try:
        page.context.storage_state(path=STATE_FILE)
        log("  Auth state saved")
    except Exception:
        pass

    log("  Login completed!")
    return True


def clear_cart(page):
    """Remove all items from the cart."""
    log("  Clearing cart...")
    page.goto(CART_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    time.sleep(3)

    # Find and click all remove buttons
    remove_selectors = [
        'button[aria-label*="Eliminar"]',
        'button[aria-label*="eliminar"]',
        'button:has-text("Eliminar")',
        '[class*="delete"], [class*="remove"]',
        'button[data-testid*="remove"]',
    ]

    for selector in remove_selectors:
        try:
            buttons = page.query_selector_all(selector)
            for btn in buttons:
                if btn.is_visible():
                    btn.click()
                    time.sleep(1)
        except Exception:
            continue

    log("  Cart cleared")


def add_to_cart(page, product_url):
    """Navigate to product page and add to cart."""
    log("Step 2: Adding product to cart...")
    page.goto(product_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    time.sleep(4)

    # Check for product not found
    content = page.content()
    if "404" in page.title() or "no encontramos" in content.lower() or "no existe" in content.lower():
        raise Exception(f"Product not found at {product_url}")

    close_popups(page)

    # Try to find and click "Agregar al Carro" button
    add_selectors = [
        'button:has-text("Agregar al Carro")',
        'button:has-text("Agregar al carro")',
        'button:has-text("AGREGAR AL CARRO")',
        'button:has-text("Add to cart")',
        'button:has-text("Add to Cart")',
        '#addToCartButton',
        'button[id*="add-to-cart"]',
        'button[data-testid*="add-to-cart"]',
        'button[class*="add-to-cart"]',
    ]

    added = False
    for selector in add_selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                btn.click()
                added = True
                log("  Clicked 'Agregar al Carro'")
                break
        except Exception:
            continue

    if not added:
        save_screenshot(page, "add_to_cart_debug")
        raise Exception(
            "Could not find 'Add to Cart' button. "
            "The product may be unavailable. Screenshot saved as add_to_cart_debug.png"
        )

    # Wait for cart confirmation / mini-cart popup
    time.sleep(3)
    close_popups(page)
    log("  Product added to cart!")
    return True


def checkout_from_cart(page):
    """Go to cart and proceed to checkout."""
    log("Step 3: Proceeding to checkout...")

    page.goto(CART_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    time.sleep(4)
    close_popups(page)

    # Check cart has items
    content = page.content()
    if "vacío" in content.lower() or "empty" in content.lower():
        raise Exception("Cart is empty!")

    # Handle "unavailable products" section - remove them if they block checkout
    unavailable = page.query_selector('text=/[Pp]roductos no disponibles/')
    if unavailable:
        log("  Note: Some products marked as unavailable")

    # Click "Continuar compra" button
    checkout_selectors = [
        'button:has-text("Continuar compra")',
        'a:has-text("Continuar compra")',
        'button:has-text("Continuar Compra")',
        'button:has-text("Ir al checkout")',
        'button:has-text("Checkout")',
        'a[href*="checkout"]',
    ]

    clicked = False
    for selector in checkout_selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                btn.click()
                clicked = True
                log("  Clicked 'Continuar compra'")
                break
        except Exception:
            continue

    if not clicked:
        save_screenshot(page, "cart_debug")
        raise Exception("Could not find checkout button in cart. Screenshot saved as cart_debug.png")

    # Wait for checkout page
    try:
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
    except PlaywrightTimeout:
        pass
    time.sleep(4)

    log("  On checkout page")
    return True


def handle_delivery(page):
    """Handle the delivery step of checkout."""
    log("Step 4: Handling delivery...")

    time.sleep(3)
    close_popups(page)

    current_url = page.url.lower()

    # If we landed on express checkout, delivery is already handled
    if "express" in current_url:
        log("  Express checkout - delivery pre-selected")
        # Just need to wait for page to fully load
        time.sleep(3)
        return True

    # If already on payment page, skip delivery
    if "payment" in current_url:
        log("  Already on payment page, skipping delivery")
        return True

    # We should be on /checkout/delivery
    # The delivery step shows: address, pickup options, delivery options

    # First, check if a delivery option is already pre-selected
    # If address is already set and delivery method selected, just click "Ir a pagar"

    # Try selecting store pickup (usually faster for test)
    pickup_clicked = False
    pickup_selectors = [
        'text="Retiro en un punto"',
        'label:has-text("Retiro en un punto")',
        'div:has-text("Retiro en un punto")',
    ]
    for selector in pickup_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                pickup_clicked = True
                log("  Selected 'Retiro en un punto'")
                time.sleep(2)

                # Select first available store
                store = page.query_selector('input[type="radio"][name*="pickup"]:not(:checked), input[type="radio"]:first-of-type')
                if store:
                    store.click()
                    log("  Selected pickup store")
                    time.sleep(1)
                break
        except Exception:
            continue

    if not pickup_clicked:
        # Try home delivery
        delivery_selectors = [
            'text="Envío a domicilio"',
            'label:has-text("Envío a domicilio")',
        ]
        for selector in delivery_selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    log("  Selected home delivery")
                    time.sleep(2)

                    # Select first available time slot
                    slot = page.query_selector('input[type="radio"][name*="shipping"]:first-of-type')
                    if slot:
                        slot.click()
                        log("  Selected delivery time slot")
                    break
            except Exception:
                continue

    time.sleep(2)

    # Click "Ir a pagar" to proceed to payment
    pay_btn_selectors = [
        'button:has-text("Ir a pagar")',
        'a:has-text("Ir a pagar")',
        'button:has-text("Ir a Pagar")',
        'button:has-text("Continuar")',
    ]

    clicked = False
    for selector in pay_btn_selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible() and btn.is_enabled():
                btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                btn.click()
                clicked = True
                log("  Clicked 'Ir a pagar'")
                break
        except Exception:
            continue

    if not clicked:
        save_screenshot(page, "delivery_debug")
        log("  WARNING: Could not click 'Ir a pagar'. Check delivery_debug.png")

    try:
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
    except PlaywrightTimeout:
        pass
    time.sleep(4)

    return True


def handle_payment(page):
    """Handle the payment step."""
    log("Step 5: Processing payment...")

    time.sleep(3)
    close_popups(page)

    current_url = page.url.lower()
    log(f"  Payment page URL: {current_url}")

    # --- SELECT CARD ---
    # The test account has saved CMR cards. Select the first CMR card.
    # From the video: CMR **** 4858 is the first card (mock enabled)

    # Check if a card is already selected (highlighted/active)
    # If not, click on the first CMR card
    cmr_selectors = [
        'div:has-text("CMR"):has-text("4858")',
        'label:has-text("CMR"):has-text("4858")',
        'div[class*="card"]:has-text("4858")',
        # Fallback to first CMR card
        'div:has-text("CMR"):first-of-type',
        'label:has-text("CMR"):first-of-type',
    ]

    for selector in cmr_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                log("  Selected CMR card")
                time.sleep(2)
                break
        except Exception:
            continue

    # --- SELECT INSTALLMENTS ---
    # Choose "Sin cuotas" (no installments) if the option appears
    time.sleep(1)
    installment_selectors = [
        'label:has-text("Sin cuotas")',
        'input[value*="sin_cuotas"]',
        'div:has-text("Sin cuotas") >> input[type="radio"]',
        'select option:has-text("Sin cuotas")',
    ]

    # Check if there's a dropdown for installments
    select_el = page.query_selector('select[name*="cuotas"], select[name*="installment"]')
    if select_el:
        try:
            select_el.select_option(index=0)  # First option is usually "Sin cuotas"
            log("  Selected 'Sin cuotas' from dropdown")
        except Exception:
            pass
    else:
        for selector in installment_selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    log("  Selected 'Sin cuotas'")
                    time.sleep(0.5)
                    break
            except Exception:
                continue

    # --- ACCEPT TERMS ---
    terms_accepted = False
    terms_selectors = [
        'label:has-text("He leído y acepto") input[type="checkbox"]',
        'label:has-text("He leído") input[type="checkbox"]',
        'label:has-text("términos") input[type="checkbox"]',
        'input[type="checkbox"][name*="terms"]',
        'input[type="checkbox"][name*="accept"]',
    ]

    for selector in terms_selectors:
        try:
            el = page.query_selector(selector)
            if el:
                if not el.is_checked():
                    el.click()
                    terms_accepted = True
                    log("  Accepted terms and conditions")
                else:
                    terms_accepted = True
                    log("  Terms already accepted")
                time.sleep(0.5)
                break
        except Exception:
            continue

    if not terms_accepted:
        # Try clicking the label text directly
        try:
            label = page.query_selector('text=/[Hh]e leído/')
            if label:
                label.click()
                log("  Clicked terms label")
                time.sleep(0.5)
        except Exception:
            log("  WARNING: Could not find terms checkbox")

    time.sleep(1)

    # --- CLICK PAY ---
    pay_selectors = [
        'button:has-text("Pagar"):not([disabled])',
        'button:has-text("Continuar"):not([disabled])',
        'button:has-text("Ir a pagar"):not([disabled])',
        'button[type="submit"]:has-text("Pagar")',
    ]

    clicked = False
    for selector in pay_selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible() and btn.is_enabled():
                btn.scroll_into_view_if_needed()
                time.sleep(1)
                btn.click()
                clicked = True
                log("  Clicked 'Pagar'")
                break
        except Exception:
            continue

    if not clicked:
        save_screenshot(page, "payment_debug")
        raise Exception("Could not click 'Pagar' button. Check payment_debug.png")

    # Wait for payment processing
    log("  Waiting for payment to process...")
    time.sleep(10)

    # Check for payment errors and retry
    max_retries = 2
    for attempt in range(max_retries):
        error_el = page.query_selector('text=/[Ee]rror en el sistema/')
        if error_el and error_el.is_visible():
            log(f"  Payment error detected (attempt {attempt + 1}/{max_retries})")
            retry_btn = page.query_selector('button:has-text("Reintentar")')
            if retry_btn and retry_btn.is_visible():
                retry_btn.click()
                log("  Clicked 'Reintentar'...")
                time.sleep(10)
            else:
                break
        else:
            break

    log("  Payment step completed")
    return True


def get_order_number(page):
    """Extract the order number from confirmation or orders page."""
    log("Step 6: Getting order number...")

    time.sleep(5)

    # Check current page for order confirmation
    content = page.content()

    # Common order number patterns
    patterns = [
        r'[Pp]edido\s*N[°o]?\s*(\d{8,12})',
        r'[Nn]úmero de pedido[:\s]*(\d{8,12})',
        r'[Oo]rder\s*(?:#|number)?[:\s]*(\d{8,12})',
        r'[Cc]ompra\s*(?:#|N[°o])?[:\s]*(\d{8,12})',
    ]

    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            order_number = match.group(1)
            log(f"  Found order number: {order_number}")
            return order_number

    # If not on confirmation page, check orders page
    log("  Checking orders page for recent order...")
    page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    time.sleep(5)
    close_popups(page)

    content = page.content()

    # Look for the most recent order number
    for pattern in patterns:
        matches = re.findall(pattern, content)
        if matches:
            order_number = matches[0]  # First match = most recent
            log(f"  Found order number: {order_number}")
            return order_number

    # Fallback: look for any 10-digit number that looks like an order
    fallback = re.findall(r'\b(\d{10})\b', content)
    if fallback:
        order_number = fallback[0]
        log(f"  Found possible order number: {order_number}")
        return order_number

    save_screenshot(page, "order_number_debug")
    log("  Could not find order number. Check order_number_debug.png")
    return None


def main():
    args = parse_args()
    email = args.account
    password = args.password

    with sync_playwright() as p:

        # --- CONNECT MODE: use existing Chrome session ---
        if args.connect:
            product_url = sku_to_url(args.sku_or_url)
            cdp_url = f"http://localhost:{args.debug_port}"
            log(f"Connecting to existing Chrome at {cdp_url}...")

            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                log(f"\nERROR: Could not connect to Chrome on port {args.debug_port}")
                log(f"Details: {e}")
                log("")
                log("Make sure you:")
                log("  1. Quit Chrome completely (Cmd+Q)")
                log("  2. Relaunch with:")
                log(f'     /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port={args.debug_port}')
                log("  3. Log into staging.falabella.com in that Chrome window")
                log("  4. Then run this script again with --connect")
                sys.exit(1)

            # Use the first existing context (your logged-in session)
            contexts = browser.contexts
            if not contexts:
                log("ERROR: No browser contexts found. Open a tab in Chrome first.")
                sys.exit(1)

            context = contexts[0]
            # Open a new tab in the existing session
            page = context.new_page()
            page.set_default_timeout(ACTION_TIMEOUT)

            log(f"Connected! Using your existing Chrome session.")
            log("")
            log("=" * 55)
            log("  Falabella FACL Staging - Order Creation (Connect Mode)")
            log("=" * 55)
            log(f"Product: {product_url}")
            log(f"Account: {email} (using existing session)")
            log("")

            try:
                # Check if already authenticated
                login(page, email, password)

                if args.clear_cart:
                    clear_cart(page)

                add_to_cart(page, product_url)
                checkout_from_cart(page)
                handle_delivery(page)
                handle_payment(page)
                order_number = get_order_number(page)

                log("")
                log("=" * 55)
                if order_number:
                    log(f"  ORDER CREATED SUCCESSFULLY!")
                    log(f"  Order Number: {order_number}")
                else:
                    log(f"  Order may have been created but number not extracted.")
                    log(f"  Check orders at: {ORDERS_URL}")
                log("=" * 55)

                save_screenshot(page, "final_result")
                return order_number

            except Exception as e:
                log(f"\nERROR: {e}")
                save_screenshot(page, "error_screenshot")
                log("Check error_screenshot.png for details.")
                sys.exit(1)

            finally:
                page.close()  # Close only the tab we opened, not the browser

        # --- STANDARD MODE: launch new browser ---
        # For --login mode, always use headed
        is_headed = args.headed or args.login

        # Launch browser with stealth settings
        browser = p.chromium.launch(
            headless=not is_headed,
            slow_mo=args.slow,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        # Create context with realistic settings
        context_options = {
            "viewport": {"width": 1280, "height": 720},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "locale": "es-CL",
            "timezone_id": "America/Santiago",
        }

        # Restore saved auth state if available (not for fresh --login)
        if os.path.exists(STATE_FILE) and not args.login:
            context_options["storage_state"] = STATE_FILE
            log("Restoring saved auth state...")

        context = browser.new_context(**context_options)

        # Remove automation indicators
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
        """)

        page = context.new_page()
        page.set_default_timeout(ACTION_TIMEOUT)

        # --- LOGIN MODE ---
        if args.login:
            try:
                interactive_login(context, page)
                return None
            finally:
                browser.close()

        # --- ORDER CREATION MODE ---
        product_url = sku_to_url(args.sku_or_url)

        log("=" * 55)
        log("  Falabella FACL Staging - Order Creation Automation")
        log("=" * 55)
        log(f"Product: {product_url}")
        log(f"Account: {email}")
        log(f"Mode:    {'headed' if args.headed else 'headless'}")
        log("")

        try:
            # Step 1: Login
            login(page, email, password)

            # Optional: Clear cart
            if args.clear_cart:
                clear_cart(page)

            # Step 2: Add product to cart
            add_to_cart(page, product_url)

            # Step 3: Proceed to checkout
            checkout_from_cart(page)

            # Step 4: Handle delivery
            handle_delivery(page)

            # Step 5: Handle payment
            handle_payment(page)

            # Step 6: Get order number
            order_number = get_order_number(page)

            # Final output
            log("")
            log("=" * 55)
            if order_number:
                log(f"  ORDER CREATED SUCCESSFULLY!")
                log(f"  Order Number: {order_number}")
            else:
                log(f"  Order may have been created but number not extracted.")
                log(f"  Check orders at: {ORDERS_URL}")
            log("=" * 55)

            save_screenshot(page, "final_result")
            return order_number

        except Exception as e:
            log(f"\nERROR: {e}")
            save_screenshot(page, "error_screenshot")
            log("Check error_screenshot.png for details.")
            sys.exit(1)

        finally:
            # Save auth state for next run
            try:
                context.storage_state(path=STATE_FILE)
            except Exception:
                pass
            browser.close()


if __name__ == "__main__":
    order = main()
    if order:
        sys.exit(0)
    else:
        sys.exit(1)
