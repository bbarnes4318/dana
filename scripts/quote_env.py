import re
from pathlib import Path

p = Path("/workspace/dana/.env")
if p.exists():
    content = p.read_text(encoding="utf-8")
    # Quote DANA_COMPANY_NAME if not quoted
    content = re.sub(r'^DANA_COMPANY_NAME\s*=\s*([^"\'].*)$', r'DANA_COMPANY_NAME="\1"', content, flags=re.MULTILINE)
    # Quote DANA_OPENING_LINE if not quoted
    content = re.sub(r'^DANA_OPENING_LINE\s*=\s*([^"\'].*)$', r'DANA_OPENING_LINE="\1"', content, flags=re.MULTILINE)
    p.write_text(content, encoding="utf-8")
    print("Quoted env vars successfully!")
else:
    print(".env file not found")
