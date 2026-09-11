"""
Structural check for site/index.html, run in CI before publishing.

A single missing </div> once nested the whole page inside the (hidden)
notification sheet: the site deployed successfully and rendered a header
above a blank screen. Nothing else catches that — the JSON is valid, the
JS is fine, the deploy is green.

    python3 site/check_layout.py site/index.html
"""

import re, sys, pathlib
from html.parser import HTMLParser

class P(HTMLParser):
    VOID = {"br","img","input","meta","link","hr","source","area","base","col"}
    def __init__(self):
        super().__init__(); self.stack=[]; self.errors=[]; self.wrap_depth=None
    def handle_starttag(self, tag, attrs):
        if tag in self.VOID: return
        d=dict(attrs)
        if d.get("class")=="wrap": self.wrap_depth=len(self.stack)
        self.stack.append(tag)
    def handle_endtag(self, tag):
        if tag in self.VOID: return
        if not self.stack or self.stack[-1]!=tag:
            self.errors.append(f"mismatched </{tag}> (open: {self.stack[-3:]})")
            if tag in self.stack:
                while self.stack and self.stack.pop()!=tag: pass
        else: self.stack.pop()

src = pathlib.Path(sys.argv[1]).read_text()
p=P(); p.feed(src)
ok=True
if p.errors:
    ok=False; print("  STRUCTURE ERRORS:"); [print("   -",e) for e in p.errors[:5]]
if p.stack:
    ok=False; print("  UNCLOSED AT EOF:", p.stack)
# body > header, div.sheet, div.wrap  => wrap must sit at depth 2 (html>body)
if p.wrap_depth is not None and p.wrap_depth != 2:
    ok=False; print(f"  .wrap is nested too deep (depth {p.wrap_depth}, expected 2)")
print("  OK" if ok else "  FAILED")
sys.exit(0 if ok else 1)
