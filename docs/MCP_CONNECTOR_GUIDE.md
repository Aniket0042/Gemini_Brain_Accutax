# The Accutax MCP connector, explained simply

*Written 12 November 2026. Describes the test setup that is running now on the VM (106.51.80.81).*

---

## 1. What it is, in one minute

The connector lets someone use **Accutax AI from inside Claude or ChatGPT**.

The user types into Claude: *"Make me a PDF of the top 5 vendors and customers for org 27, with charts."*
Claude asks Accutax for the figures, Accutax sends back the numbers, and Claude builds the PDF.

**Analogy: a translator with a phone line to the accountant.**
- Claude (or ChatGPT) is a smart assistant who talks to the user, writes reports and draws charts, but
  knows nothing about the user's books.
- Accutax AI is the accountant who has the books.
- The connector is the phone line between them. The assistant can only ask questions. It cannot
  change anything in the books.

**MCP** ("Model Context Protocol") is just the standard language that Claude and ChatGPT use to talk to
outside tools. It works like a common plug shape, so one connector works with both apps.

---

## 2. The pieces

| Piece | What it is | Where it runs |
|---|---|---|
| Claude / ChatGPT | The AI app the user talks to | Anthropic / OpenAI servers |
| Cloudflare tunnel | A secure pipe from the internet to our VM | Cloudflare + a small program on the VM |
| MCP server | Our connector: sign-in pages and the two tools | VM, port 8012 (`accutax-mcp` service) |
| Accutax AI API | The same AI the portal uses | VM, port 8011 (`accutax-ai-api` service) |
| Accutax backend | Checks the user's email and password | VM, port 3001 |
| Database + Cube | The books themselves | DB server + VM |

```
User ─► Claude/ChatGPT ─► Cloudflare ─► tunnel ─► MCP server ─► Accutax AI API ─► books
                                         (VM)        (8012)          (8011)
```

The MCP server is a thin front door. It does not have its own copy of the books or its own rules about
who may see what. It passes every question to the same Accutax AI that the portal uses, **as the signed-in
user**, so the same access rules apply.

---

## 3. What Claude can do with it (the two tools)

1. **List organizations**: "which companies can this user see?" Returns names, ids and currencies.
2. **Ask Accutax**: one question in plain words, for one or up to 10 organizations at once.
   It can choose a specialist: VAT, bills and payables (AP), cash, or general.

Both tools are **read-only**. There is no tool to create, edit, file, pay or delete anything.

**What comes back:** text and tables only (the answer, its tables and FTA law sources). No SQL, no
internal traces, no cost figures. Charts, PDFs and spreadsheets are drawn **by Claude/ChatGPT** from those
figures, not by Accutax. That is why the PDF build time is on Claude's side.

---

## 4. Setting it up in Claude (what the user sees)

1. In Claude: **Settings → Connectors → Add custom connector**, paste the link
   (now `https://portfolio-harley-mens-someone.trycloudflare.com/mcp`).
2. Click **Connect**. A new browser tab opens with the **Accutax sign-in page** (green, Accutax logo).
3. Type the **same email and password as the Accutax app**, click **Sign in and allow**.
4. The tab closes and Claude says the connector is connected.
5. In the connector settings, each tool can be set to **Always allow**, **Needs approval** (Claude asks
   before every call) or **Block**. Both tools only read, so "Always allow" is safe.

ChatGPT works the same way (its "connectors" / developer mode). It has not been tested yet.

---

## 5. Sign-in, step by step (the "hotel key card")

**Analogy.** At a hotel you show your ID once at the front desk and get a key card. The card opens your
room for a while. The front desk keeps your ID details; the card itself is just a random code.
If the card expires, the desk quietly gives you a new one as long as your stay is still valid.

What actually happens the first time:

1. **Claude knocks** on our server without a card. Our server answers "401: please sign in, here is
   where".
2. **Claude reads our sign-in rules** (two small public "about me" files at `/.well-known/...`).
3. **Claude registers itself** and gets an app id. Our server only accepts apps whose return address is
   `claude.ai`, `claude.com`, `chatgpt.com`, `chat.openai.com` (or localhost for testing). Any other app
   is refused here.
4. **Claude opens our sign-in page** in your browser. The page link carries a one-time ticket that
   lasts **10 minutes** (that is the "This sign-in link has expired" page if you open an old or bare
   link).
5. **You type your Accutax email and password on our page.** Claude never sees them. Our server checks
   them with the Accutax login, the same one the portal uses. After **5 wrong tries** the ticket is
   cancelled.
6. You click **Allow**. Our server sends the browser back to Claude with a **one-time code** that lasts
   **5 minutes** and works once.
7. **Claude swaps the code for the key card** (an *access token*) plus a *refresh token*. A PKCE check
   proves that the Claude swapping the code is the same Claude that started the sign-in, so a stolen
   code is useless.

After that:

- **Access token** (the key card): lasts **60 minutes**.
- **Refresh token** (the "stay is still valid" note): lasts **30 days**. Claude uses it to get a new key
  card silently. After 30 days the user signs in again.
- That is why removing and re-adding the connector did not ask for a password: Claude still had a valid
  card. **Disconnect** in Claude's settings makes Claude forget it.

Standard names, if you need them: OAuth 2.1, Dynamic Client Registration, PKCE. It is the same kind of
"Sign in with ..." flow that Google, Slack or GitHub connectors use. We built our own small version instead
of buying Auth0/WorkOS.

### What happens on every question

1. Claude calls **Ask Accutax** and shows its key card.
2. Our server looks the card up: which user is it, is it still valid?
3. Behind each card our server keeps a matching **Accutax AI pass** for that user (a short-lived signed
   token). It sends the question to the Accutax AI with that pass, exactly as if the user had asked in the
   portal.
4. The Accutax AI checks which organizations the user may see and answers only for those.

### Where the cards are stored

- On the VM in `backend/data/mcp/` (`clients.enc`, `tokens.enc`), **encrypted** (Fernet).
- Only a fingerprint (hash) of each card is stored, never the card itself, so a stolen file does not
  give working cards.
- Passwords are never stored.

### Current limit

Sign-in works with the normal Accutax email-and-password login (what the VM uses). The newer
SSO / gateway login (zereco) does not work with it yet. That needs a small piece of work with the backend
developer. The same piece of work could also allow "already logged in to the portal, just click Allow".

---

## 6. Security, layer by layer

Think of a bank branch: a locked street door, a guard who checks ID, a teller who only shows your own
accounts, a camera, and rules like "one customer at a time".

| Layer | What protects you | Simple version |
|---|---|---|
| Transport | HTTPS from Claude to Cloudflare; encrypted tunnel from Cloudflare to the VM | Nobody on the internet can read the traffic |
| No open doors | The VM opens **no** ports for this. The tunnel program on the VM dials **out** to Cloudflare | Like a phone that only makes calls, never takes them |
| Host check | The server only answers requests addressed to its own name (blocks "DNS rebinding" tricks) | The guard only serves people who came to *this* branch |
| Sign-in | Our page, our password check, approved apps only, PKCE, short codes | ID check at the desk |
| Cards | Random, short-lived, stored only as fingerprints, encrypted on disk | Key card, not a copy of your ID |
| Same access rules | Every question goes through the same Accutax AI checks as the portal | The teller shows only *your* accounts |
| Read only | No write tools exist | The teller can show, never move money |
| Limits | 20 tool calls a minute per user; 15 questions a minute; one question at a time per user; up to 10 orgs per call | "One customer at a time, please" |
| Size and time caps | Answers capped at 24,000 characters and 50 rows per table; Accutax works up to 75 seconds per question | Stops runaway answers |
| Prompt-injection guard | If data (e.g. an invoice note) contains text like "ignore your instructions", the answer carries a warning so Claude treats it as data | A label saying "this is a customer's note, not an order" |
| Audit log | Every call is written to `logs/mcp_audit.jsonl`: who, which tool, which orgs, time taken, outcome. The question text is **not** stored, only a fingerprint | The camera, without recording the conversation |

### Privacy: what leaves Accutax

- The **answer text and tables** go to Claude (Anthropic) or ChatGPT (OpenAI). Those companies then hold
  that text under the user's own Claude/ChatGPT account and its data settings.
- **Real names** of customers and vendors are shown for every organization (you chose this on 10 Nov;
  setting `MCP_FULL_DETAIL_ORGS=*`). The safer default was masking names (e.g. `[CUSTOMER_1]`); it can be
  switched back per organization.
- What never leaves: SQL, internal traces, cost data, files from the portal.

### Honest weak points today

- **Test link.** The trycloudflare link is a free, temporary test link (see section 7).
- **Cloudflare sees the traffic.** Cloudflare decrypts HTTPS at its edge and re-encrypts it into the
  tunnel. This is normal for any site behind Cloudflare, but it means Cloudflare is a party you trust.
- **Plain HTTP inside.** The portal (`:5558`) and AI site (`:3535`) are still plain HTTP. The connector
  itself is HTTPS end-to-end from the user's side.
- **No "sign out everywhere" button yet.** Cancelling a user's cards is a manual step on the VM.
- **Answer quality** depends on Accutax AI. Example: for "when is a VAT return due" the VAT agent did not
  find the right FTA guide; Claude corrected it. That is an Accutax AI fix, not a connector fix.

---

## 7. Cloudflare and the domain

### Why we need it

Claude and ChatGPT only talk to an **HTTPS address on the public internet**. Our VM has no public HTTPS:
its router only forwards ports 3434, 3535, 5558 and 5769, ports 80/443 are closed, and you do not have the
authority to open them. So we need a way **in** without opening a door.

### How the tunnel works (analogy)

Imagine our office has no front door on the street. Instead, someone inside phones a **reception
service** (Cloudflare) and keeps the line open. Visitors go to the reception service's public desk; the
receptionist passes their messages down the open phone line, and passes the answers back.

- The phone call is started **from inside** (the VM dials out). That is why no port needs opening.
- The program doing this is **cloudflared**, running as the service `accutax-mcp-tunnel`.
- It forwards everything to `127.0.0.1:8012` on the VM, which is the MCP server and nothing else. The
  portal, the database and SSH are **not** reachable through the tunnel.

```
Claude ──HTTPS──► Cloudflare edge ◄══ encrypted tunnel (opened by the VM) ══ cloudflared ──► 127.0.0.1:8012
```

### The free "quick tunnel" we use now

- Address: `https://portfolio-harley-mens-someone.trycloudflare.com`. Cloudflare picked those random
  words.
- **Free, no account needed.** Made for testing.
- **The address changes whenever the tunnel restarts** (VM reboot, service restart). Then:
  1. read the new address from the tunnel log;
  2. put it in `/opt/accutax-ai/mcp.env` (`MCP_PUBLIC_URL`, `MCP_ALLOWED_HOSTS`);
  3. restart `accutax-mcp`;
  4. every user re-adds the connector in Claude with the new link.
- Cloudflare gives **no uptime promise** for quick tunnels and limits their traffic.
- The icon Claude shows may be Cloudflare's, because the main domain is `trycloudflare.com`. We serve the
  Accutax "A" icon, but Claude may not use it until we use our own domain.

### Is the free tunnel safe?

**For testing, yes.** The address being random is **not** what keeps it safe; anyone who learns the link
still meets the sign-in page and needs a real Accutax login. Safety comes from section 6 (sign-in, cards,
same access rules, read only), and the link is HTTPS.

**For real customers, no.** Not because it leaks data, but because:
- the address changes on restart, so everyone's connector breaks;
- there is no uptime promise;
- the domain is not ours, so it does not look trustworthy, and the icon and name are Cloudflare's;
- we cannot add our own Cloudflare protections (firewall rules, rate limits, logs).

### The plan: our own address (`mcp.tersum.in`, later an Accutax domain)

`tersum.in` is your domain. It is registered at BigRock and its DNS is run by Netlify, which hosts the
website. DNS works like the internet's phone book: it says which server each name goes to.

Steps:
1. Create a free Cloudflare account and add `tersum.in`. Cloudflare copies the existing records, so the
   Netlify site keeps working (check this before switching).
2. At BigRock, change the domain's **nameservers** to the two Cloudflare gives. This moves the phone
   book to Cloudflare. The website stays on Netlify.
3. On the VM, replace the quick tunnel with a **named tunnel** linked to that account, and point
   `mcp.tersum.in` at it. Cloudflare creates that DNS record itself.
4. Set `MCP_PUBLIC_URL=https://mcp.tersum.in/mcp`, restart, re-add the connector once.

After that the address never changes, the HTTPS certificate is automatic and free, and we can add
Cloudflare firewall and rate rules. For launch the same steps work for an Accutax-owned domain (e.g.
`mcp.accutax.ai`).

**Alternative without Cloudflare:** open ports 80/443 on the VM's router and use nginx + a free
Let's Encrypt certificate. This needs the network admin.

---

## 8. A question's full journey (example)

*"Top 5 vendors and customers for org 27, as a PDF with charts."*

1. Claude reads the connector's instructions: list orgs first, one topic per question, several
   organizations in **one** call, never parallel calls, draw charts yourself.
2. Claude calls **List organizations** and sees org 27 (about 0.1 s).
3. Claude calls **Ask Accutax** with focused questions, one at a time: top vendors, top customers,
   monthly trend, and so on.
4. Each call: Cloudflare → tunnel → MCP server checks the card and the per-minute limit → Accutax AI
   (as the user) → Cube/DB → answer as text and tables → audit line written → back to Claude.
5. Measured on 12 Nov: 6 calls of 7–28 s each, about 88 s of Accutax time in total, no time-outs
   (before the fixes, the same kind of request took 127 s and 2 calls timed out).
6. Claude writes the PDF and draws the charts **on its own side**. Our logs cannot see that part.

---

## 9. Where things live (for the team)

| What | Where |
|---|---|
| Code | `backend/src/gemini_brain/mcp_server/` (`server.py` tools and limits, `oauth.py` sign-in, `login_page.py` page design, `icons.py`, `audit.py`, `upstream.py`) |
| Privacy view of answers | `backend/src/gemini_brain/privacy/external.py` |
| Settings | `backend/src/gemini_brain/config/settings.py` (`MCP_*`) |
| Tests | `backend/tests/test_mcp_server.py`, `backend/tests/test_mcp_oauth.py` |
| VM services | `accutax-mcp` (server), `accutax-mcp-tunnel` (cloudflared), `accutax-ai-api` (Accutax AI) |
| VM config | `/opt/accutax-ai/mcp.env`; real-names switch also in `/opt/accutax-ai/.env` |
| Cards (encrypted) | `/opt/accutax-ai/backend/data/mcp/` |
| Audit log | `/opt/accutax-ai/backend/logs/mcp_audit.jsonl` |
| Evaluations | `docs/MCP_EVAL_20261110.md` (89 real questions: 76 pass, 8 partial, 0 fail, 5 not applicable) |
| Plan | Plan doc artifact, phases 0–6 |

---

## 10. What is next

1. **Stable address**: move `tersum.in` DNS to Cloudflare and use a named tunnel (`mcp.tersum.in`).
2. **Test ChatGPT** end to end.
3. **SSO / gateway sign-in** with the backend developer, including "already logged in to the portal,
   just click Allow".
4. **"Sign out everywhere"** for a user (cancel all their cards).
5. **Warm-up job** so the first question after a long idle period is not slow.
6. **VAT knowledge fix** for basic filing questions (due dates, monthly vs quarterly).
7. **Pilot** with a few real users, then the Claude and ChatGPT connector directories.

## Glossary

- **MCP**: the standard "plug" Claude and ChatGPT use to talk to outside tools.
- **Connector**: Claude/ChatGPT's name for an MCP server you add.
- **Tool**: one action the connector offers (here: List organizations, Ask Accutax).
- **OAuth**: the standard "Sign in with ... and click Allow" process.
- **Access token**: the key card (60 minutes). **Refresh token**: lets Claude get a new key card (30 days).
- **PKCE**: a check that the app finishing the sign-in is the same one that started it.
- **Tunnel**: an outgoing pipe that lets the internet reach one service on the VM without opening ports.
- **Quick tunnel**: Cloudflare's free, temporary, random-address tunnel for testing.
- **Named tunnel**: the permanent version, tied to our account and our domain.
- **DNS / nameservers**: the internet's phone book, and who runs it for a domain.
- **Audit log**: the record of every call, without the question text.
