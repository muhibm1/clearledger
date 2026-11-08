# Finance desk

One page over the warehouse that puts both sides of each customer together: the CRM side (open
pipeline, deals won) and the ERP side (invoices, payments, what is late). The column that needs
both is "won, not yet cash": deals sales has closed that finance has not collected.

```
.venv/bin/python -m crm.server        # http://127.0.0.1:4320
```

- Click a customer, or press ⌘K, for their sidebar: each won deal followed to cash, the open
  pipeline, invoices, payments and an aging bar. Read-only; changes are made in Odoo.
- **Talk** starts a voice call with the agent in `voice/`. The page runs each tool the agent calls
  through this server, opens the customer it is talking about and lights the records behind its
  answer. It needs the agent deployed (`python -m voice.deploy`). With `ELEVENLABS_API_KEY` set where
  the server runs, the page gets a short-lived signed address for one call and the key stays on the
  server. Without a key the page connects by agent id, which works only while the agent does not
  require signed links.
- The server only answers this machine (it binds 127.0.0.1 and refuses other host names), because
  the page can run the warehouse tools.

The built page is committed in `dist/`. To change it: `cd crm && npm ci && npm run build`. The
colors, buttons, badges and slide-in panel come from Auspex.
