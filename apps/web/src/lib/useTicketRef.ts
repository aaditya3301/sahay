import { useQuery } from '@tanstack/react-query'
import { api, auth } from './api'

// Each business numbers its own tickets (LL-1042). Returns a formatter using the workspace's prefix.
export function useTicketRef() {
  const tenant = auth.get()?.tenant
  const { data: me } = useQuery({ queryKey: ['me', tenant], queryFn: () => api('/v1/me') })
  const prefix = me?.ticket_prefix || 'T'
  return (n?: number | string | null) => (n == null || n === '' ? '' : `${prefix}-${n}`)
}
