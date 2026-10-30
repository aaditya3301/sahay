export interface SahayUser { email?: string; phone?: string; name?: string; id?: string }
export interface SahayContext { productSku?: string; productTitle?: string; cart?: unknown; [key: string]: unknown }
export interface SahayOptions {
  /** Public site key from Agent Studio → Website widget (pk_...). Works only on your allowed domains. */
  siteKey: string
  /** Your Sahay host. Defaults to the hosted Sahay app. */
  host?: string
  /** Logged-in shopper. Only trusted when userHash is valid. */
  user?: SahayUser
  /** HMAC-SHA256(secret_key, lowercase email or phone), computed on YOUR server. */
  userHash?: string
  /** What the visitor is looking at; the agent uses it with live price and stock. */
  context?: SahayContext
  /** Open the chat as soon as it loads. */
  open?: boolean
}
export interface SahayCart { items: { sku: string; qty: number }[]; phone?: string; email?: string; name?: string; checkoutUrl?: string }
export type SahayEvent = 'lead_captured' | 'message' | 'cart_saved'
export function init(options: SahayOptions): void
export function setContext(context: SahayContext): void
export function identify(user: SahayUser, userHash: string): void
export function open(): void
export function close(): void
export function on(event: SahayEvent, handler: (data: unknown) => void): void
export function cart(data: SahayCart): void
