import sys
sys.path.insert(0, r'D:\qclaw\resources\openclaw\config\skills\browser-cdp\scripts')
from cdp_client import CDPClient
from browser_actions import BrowserActions
from page_snapshot import PageSnapshot

client = CDPClient("http://127.0.0.1:54380")
client.connect()

# Check existing tabs
tabs = client.list_tabs()
print(f"Tabs: {len(tabs)}")
for t in tabs:
    print(f"  {t['id']}: {t.get('url','?')}")

# Navigate to Gradio
tab = client.create_tab("http://127.0.0.1:7860")
client.attach(tab['id'])

actions = BrowserActions(client, PageSnapshot(client))
actions.wait_for_load(timeout=10)

import time
time.sleep(2)

# Screenshot
actions.screenshot(r"C:\Users\Kanyun\.qclaw\workspace\mesa-econ\_screenshot.png", full_page=False)
print("Screenshot saved")
