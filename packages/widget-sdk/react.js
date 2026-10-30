import { useEffect } from 'react'
import { init, setContext } from './index.js'

/** <SahayChat siteKey="pk_..." user={{ email }} userHash={hash} context={{ productSku }} /> */
export function SahayChat({ siteKey, host, user, userHash, context, open }) {
  useEffect(() => { init({ siteKey, host, user, userHash, context, open }) }, [siteKey])
  useEffect(() => { if (context) setContext(context) }, [JSON.stringify(context || {})])
  return null
}
export default SahayChat
