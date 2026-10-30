# @sahay/widget

Your business's AI sales & support chat, on your own website.

```bash
npm i @sahay/widget
```

```js
import { init, setContext, on } from '@sahay/widget'

init({ siteKey: 'pk_yourbrand_...', user: { email: 'riya@example.com' }, userHash: '<from your server>' })
setContext({ productSku: 'CHM-SET-07' })          // the agent now knows what they're looking at
on('lead_captured', () => analytics.track('lead'))
```

React:

```jsx
import { SahayChat } from '@sahay/widget/react'
<SahayChat siteKey="pk_yourbrand_..." context={{ productSku: product.sku }} />
```

## Keys

- **Site key (`pk_…`)** is public. It only works on the domains you list in Agent Studio → Website widget.
- **Secret key (`sk_…`)** stays on your server. Use it to sign logged-in shoppers so the agent can show their orders:

```js
// Node
const userHash = crypto.createHmac('sha256', process.env.SAHAY_SECRET_KEY).update(email.toLowerCase()).digest('hex')
```

```python
# Python
user_hash = hmac.new(SAHAY_SECRET_KEY.encode(), email.lower().encode(), hashlib.sha256).hexdigest()
```

Anonymous visitors can browse and ask about products. To see order details they verify their phone with an OTP inside the chat.
