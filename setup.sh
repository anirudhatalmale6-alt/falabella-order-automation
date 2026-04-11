#!/bin/bash
# Setup script for Falabella Order Creation Automation
# Run this once on your Mac to install dependencies

echo "=== Setting up Falabella Order Automation ==="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is required. Install from https://www.python.org/"
    exit 1
fi

echo "Python: $(python3 --version)"

# Install pip dependencies
echo ""
echo "Installing Python dependencies..."
pip3 install playwright

# Install Playwright browsers (Chromium only)
echo ""
echo "Installing Chromium browser for Playwright..."
python3 -m playwright install chromium

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Usage:"
echo "  python3 create_order.py <SKU>              # Headless mode"
echo "  python3 create_order.py <SKU> --headed      # See browser window"
echo "  python3 create_order.py <URL> --headed      # Use full product URL"
echo ""
echo "Examples:"
echo "  python3 create_order.py 7144554"
echo "  python3 create_order.py 881333143 --headed"
echo ""
echo "NOTE: You must be connected to your corporate VPN to access staging.falabella.com"
