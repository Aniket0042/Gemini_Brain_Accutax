"""HTML for the connector's sign-in page, styled like the Accutax sign-in page.

Accutax colours (frontend src/index.css): green #109383, dark green #0D7A6D, banner gradient
#125D56 to #0E9384, text #181D27 / #535862 / #414651, borders #D5D7DA. The page loads nothing from
outside (the CSP is default-src 'none'), so the logo is inline SVG and the font falls back to the system
sans-serif when Noto Sans is not installed.
"""

LOGO = '<svg role="img" aria-label="Accutax.ai" width="198" height="33" viewBox="0 0 198 33" fill="none" ><g clip-path="url(#clip0_1148_21378)"><path d="M-4.2066e-05 31.6197C3.29425 22.9485 6.51177 14.4692 9.73888 5.99237C10.1924 4.80229 10.3723 3.17554 11.2433 2.57571C12.119 1.97347 13.705 2.38376 14.979 2.39096C16.0467 2.39576 17.3472 2.10304 18.1126 2.6021C18.8252 3.06757 18.9859 4.3992 19.3554 5.36134C22.7049 14.0494 26.0472 22.7398 29.4639 31.6125C27.1797 31.6125 24.9675 31.6341 22.7553 31.5789C22.537 31.5741 22.2466 31.1447 22.1291 30.8543C21.5316 29.3787 20.951 27.8959 20.4231 26.394C20.1928 25.7413 19.8785 25.4558 19.1371 25.4678C15.9412 25.511 12.7429 25.5086 9.54453 25.4678C8.85112 25.4582 8.54881 25.7101 8.36166 26.3316C7.89859 27.8528 7.41872 29.3739 6.87167 30.8663C6.7565 31.1782 6.32462 31.5741 6.0271 31.5861C4.07884 31.6509 2.12577 31.6197 -0.00244141 31.6197H-4.2066e-05ZM18.6212 20.6139C17.1744 16.156 15.7708 11.8276 14.4704 7.81347C13.2011 11.8132 11.8167 16.168 10.4035 20.6139H18.6212Z" fill="#15B79E" /><path d="M122.347 14.8435V12.2018H127.547V31.5645H122.186V29.1316C121.896 29.3667 121.764 29.4387 121.675 29.5467C119.218 32.5267 116.018 33.0497 112.582 31.9484C109.055 30.8183 107.45 27.9463 106.867 24.4721C106.368 21.5041 106.639 18.6105 107.934 15.8297C110.319 10.7167 118.343 9.88648 121.51 14.3133C121.663 14.5292 121.862 14.7116 122.04 14.9107C122.143 14.8891 122.246 14.8675 122.347 14.8459V14.8435ZM112.368 21.8088C112.536 22.6246 112.637 23.4596 112.882 24.2513C113.568 26.4635 115.283 27.6248 117.69 27.584C119.977 27.5456 121.75 26.286 122.318 24.1458C122.561 23.234 122.681 22.2503 122.635 21.3073C122.453 17.3916 119.602 15.2106 116.03 16.1752C113.825 16.7702 112.467 18.908 112.366 21.8064L112.368 21.8088Z" fill="#15B79E" /><path d="M185.903 31.7085H180.483V29.3619C180.313 29.3787 180.212 29.3595 180.181 29.3979C175.785 34.5541 168.081 33.2729 165.672 26.929C164.396 23.5627 164.441 20.1029 165.672 16.7294C166.759 13.7542 168.954 11.9499 172.109 11.4797C175.293 11.0046 178.062 11.7556 180.032 14.5172C180.099 14.6108 180.181 14.6924 180.445 15.0019V12.0819H185.901V31.7085H185.903ZM180.733 21.8256C180.728 19.0256 179.569 17.0389 177.527 16.3359C174.835 15.4098 172.148 16.6742 171.234 19.2991C170.063 22.663 171.296 26.2428 173.95 27.1689C174.098 27.2217 174.254 27.2553 174.41 27.2913C178.189 28.1551 180.742 25.9501 180.735 21.8256H180.733Z" fill="#353535" /><path d="M70.1207 12.1755H75.3849C75.3849 12.5953 75.3825 13.0152 75.3849 13.4327C75.416 16.8278 75.3609 20.2252 75.512 23.6131C75.6296 26.2284 77.0524 27.3561 79.7948 27.3513C82.4485 27.3465 83.8857 26.1924 84.0321 23.6779C84.1832 21.089 84.1352 18.4905 84.164 15.8944C84.1784 14.6972 84.164 13.4999 84.164 12.221H89.9009C89.7617 16.6694 89.8289 21.1394 89.4234 25.5662C89.0899 29.2204 86.7385 31.399 83.1083 32.1092C80.6418 32.5938 78.1801 32.5602 75.7591 31.8932C72.4529 30.9815 70.3438 28.5749 70.1663 24.9447C69.9623 20.7267 70.1183 16.4919 70.1183 12.1707L70.1207 12.1755Z" fill="#15B79E" /><path d="M130.546 12.1347C132.782 12.1347 134.816 12.1035 136.849 12.1659C137.177 12.1755 137.626 12.5354 137.796 12.8521C138.766 14.6564 139.67 16.4991 140.616 18.3681C141.599 16.5806 142.605 14.8411 143.519 13.0584C143.905 12.3074 144.395 12.0771 145.213 12.1059C146.928 12.1683 148.649 12.1251 150.511 12.1251C150.331 12.449 150.211 12.7129 150.05 12.9432C148.217 15.5585 146.4 18.1858 144.527 20.7723C144.076 21.3961 144.073 21.8328 144.534 22.447C145.633 23.9154 146.66 25.4414 147.713 26.9458C148.766 28.4478 149.817 29.9522 150.988 31.6245C148.733 31.6245 146.612 31.6413 144.491 31.5981C144.26 31.5933 143.958 31.2838 143.826 31.0415C142.84 29.2228 141.89 27.3825 140.925 25.5518C140.822 25.3574 140.697 25.1751 140.601 25.0167C139.45 26.977 138.305 28.8773 137.211 30.8063C136.88 31.3918 136.513 31.6605 135.803 31.6389C134.005 31.5837 132.206 31.6197 130.21 31.6197C130.639 30.9911 131.001 30.4512 131.373 29.921C133.063 27.4976 134.732 25.0599 136.462 22.6678C136.957 21.984 136.983 21.4993 136.484 20.7915C134.505 17.9867 132.597 15.1362 130.55 12.1371L130.546 12.1347Z" fill="#15B79E" /><path d="M65.4299 25.5518C65.4299 27.1977 65.3795 28.7141 65.4515 30.2257C65.4875 30.9767 65.1636 31.2814 64.5446 31.5357C58.3207 34.0958 50.4388 30.9095 49.8966 22.7901C49.5103 17.0005 52.7158 12.5737 58.2367 11.6212C60.2905 11.2661 62.366 11.3189 64.3598 12.1467C64.9908 12.4082 65.31 12.7225 65.2812 13.4591C65.2236 14.9251 65.2644 16.3935 65.2644 17.9099C62.0109 15.7529 58.6302 15.8225 56.7467 18.037C55.0384 20.0453 55.0912 23.7331 56.8595 25.6741C58.8653 27.8791 62.2436 27.8719 65.4323 25.5518H65.4299Z" fill="#15B79E" /><path d="M46.6167 25.5446C46.6167 27.1737 46.5687 28.6517 46.6359 30.1249C46.6718 30.9023 46.3647 31.2814 45.6809 31.5405C41.8708 32.9777 38.2046 32.7642 34.9151 30.2833C31.6016 27.7856 30.6275 24.2201 31.1361 20.2636C32.187 12.0771 40.3208 10.148 45.525 12.1539C46.2208 12.4226 46.5159 12.7801 46.4823 13.5551C46.4175 14.9803 46.4631 16.4103 46.4631 17.8643C42.8065 15.7289 39.6322 15.856 37.8063 18.1858C36.1555 20.2924 36.2755 23.7907 38.063 25.7077C40.1001 27.8935 43.3847 27.8647 46.6167 25.5446Z" fill="#15B79E" /><path d="M101.648 12.1227H104.285V16.3623H101.701V31.5429H96.1632V16.4559H93.4663V12.1971C93.795 12.1803 94.1405 12.1467 94.486 12.1443C95.5865 12.1347 96.1376 11.5628 96.1392 10.4287C96.1392 8.87638 96.1392 7.324 96.1392 5.68765H101.646V12.1227H101.648Z" fill="#15B79E" /><path d="M197.235 31.6989H191.727V12.0819H197.235V31.6989Z" fill="#353535" /><path d="M160.542 28.8965C160.533 30.7944 159.016 32.3011 157.118 32.3035C155.196 32.3035 153.591 30.6864 153.644 28.7957C153.694 26.917 155.247 25.379 157.099 25.3719C159.011 25.3647 160.554 26.9434 160.542 28.8989V28.8965Z" fill="#353535" /><path d="M197.703 3.90016C197.703 5.80043 196.3 7.25203 194.443 7.27123C192.648 7.28802 191.074 5.72845 191.069 3.93135C191.067 2.09585 192.564 0.603463 194.419 0.589067C196.326 0.57707 197.701 1.96149 197.703 3.90016Z" fill="#353535" /></g><defs><clipPath id="clip0_1148_21378"><rect width="197.703" height="32" fill="white" transform="translate(0 0.58905)" /></clipPath></defs></svg>'

# Double braces are literal CSS braces: these strings go through str.format.
_STYLE = """
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.5 "Noto Sans",system-ui,-apple-system,"Segoe UI",sans-serif;color:#181D27;background:#fff;
  min-height:100vh;display:flex}}
.banner{{flex:3;background:linear-gradient(45deg,#125D56,#0E9384);color:#fff;display:flex;flex-direction:column;
  justify-content:center;padding:64px 8%;position:relative;overflow:hidden}}
.banner:after{{content:"";position:absolute;right:-120px;bottom:-120px;width:420px;height:420px;border-radius:50%;
  border:60px solid rgba(255,255,255,.06)}}
.banner h2{{font-size:38px;line-height:1.3;font-weight:600;max-width:560px}}
.banner p{{margin-top:20px;font-size:16px;max-width:480px;opacity:.9}}
.banner ul{{margin-top:28px;list-style:none;display:grid;gap:12px;font-size:15px}}
.banner li{{display:flex;gap:10px;align-items:flex-start}}
.banner li:before{{content:"";flex:none;width:18px;height:18px;margin-top:2px;border-radius:50%;
  background:#99F6E0 no-repeat center/10px;box-shadow:inset 0 0 0 5px #99F6E0,inset 0 0 0 9px #0D7A6D}}
.side{{flex:2;display:flex;align-items:center;justify-content:center;padding:32px 16px}}
main{{width:100%;max-width:400px}}
.logo svg{{width:170px;height:auto}}
h1{{font-size:24px;font-weight:600;margin:24px 0 6px}}
.lead{{color:#535862;font-size:14px;margin-bottom:20px}}
.consent{{background:#F0FDF9;border:1px solid #99F6E0;border-radius:8px;padding:12px 14px;font-size:13px;
  color:#414651;margin-bottom:20px}}
.consent b{{color:#0D7A6D}}
.consent div+div{{margin-top:4px}}
label{{display:block;font-size:14px;margin:0 0 4px}}
.field{{position:relative;margin-bottom:16px}}
.field svg{{position:absolute;left:14px;top:50%;transform:translateY(-50%);pointer-events:none}}
input{{width:100%;border:1px solid #D5D7DA;border-radius:8px;padding:9px 14px 9px 40px;font:inherit;font-size:14px;
  color:#181D27;outline:none;background:#fff}}
input:focus{{border-color:#109383;box-shadow:0 0 0 3px rgba(16,147,131,.15)}}
input::placeholder{{color:#717680}}
.row{{display:flex;flex-direction:row-reverse;gap:10px;margin-top:8px}}
button{{flex:1;padding:9px;border-radius:8px;font:inherit;font-size:14px;font-weight:500;cursor:pointer;
  transition:background-color .3s}}
.ok{{background:#109383;color:#fff;border:0}}.ok:hover{{background:#000}}
.no{{background:#fff;color:#414651;border:1px solid #D5D7DA;font-weight:600}}.no:hover{{background:#F9FAFB}}
.err{{color:#ef4444;text-align:center;font-size:14px;margin-bottom:16px}}
.foot{{margin-top:20px;font-size:12px;color:#717680;text-align:center}}
@media (max-width:900px){{.banner{{display:none}}.side{{align-items:flex-start;padding-top:8vh}}}}
"""

_BANNER = """<aside class="banner" aria-hidden="true">
<h2>Ask Accutax AI from your AI assistant</h2>
<p>Use your Accutax books in Claude or ChatGPT: sales, purchases, VAT and reports, with the same access you have
in Accutax.</p>
<ul><li>Read only: nothing is changed, filed or paid</li><li>Only the organisations you can already see</li>
<li>Disconnect at any time</li></ul></aside>"""

_MAIL = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#717680" stroke-width="1.8" '
         'stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/>'
         '<path d="m3 7 9 6 9-6"/></svg>')
_LOCK = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#717680" stroke-width="1.8" '
         'stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/>'
         '<path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>')

_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
         '<style>' + _STYLE + '</style></head><body>' + _BANNER + '<div class="side"><main>'
         '<div class="logo">' + LOGO + '</div>')
_TAIL = '</main></div></body></html>'

PAGE = _HEAD.replace("{title}", "Sign in to Accutax") + """
<h1>Connect {client} to Accutax</h1>
<p class="lead">Log in with your Accutax account to let {client} read your data and ask Accutax AI questions.</p>
<div class="consent"><div><b>Read only.</b> {client} cannot change, file or pay anything.</div>
<div>Returns to: <b>{host}</b></div></div>
{error}
<form method="post" action="login">
<input type="hidden" name="txn" value="{txn}">
<label for="email">Email</label>
<div class="field">""" + _MAIL + """<input id="email" name="email" type="email" autocomplete="username"
 placeholder="Enter your email" required value="{email}"></div>
<label for="password">Password</label>
<div class="field">""" + _LOCK + """<input id="password" name="password" type="password"
 autocomplete="current-password" placeholder="Enter your password" required></div>
<!-- Allow comes first in the page: Enter in a field submits with the first button. Cancel is drawn on the left. -->
<div class="row"><button class="ok" name="action" value="allow">Sign in and allow</button>
<button class="no" name="action" value="deny" formnovalidate>Cancel</button></div>
</form>
<p class="foot">Same email and password as the Accutax app.</p>""" + _TAIL

GONE = (_HEAD.replace("{title}", "Accutax") + """
<h1>Sign-in link expired</h1>
<p class="lead">This sign-in link has expired. Start the connection again from your AI app.</p>
<p class="foot">Links work once and last a few minutes. Open your AI app's connector settings and choose
Connect again.</p>""" + _TAIL).replace("{{", "{").replace("}}", "}")
