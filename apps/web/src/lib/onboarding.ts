export type ReplyMode = 'draft' | 'auto'
export type Goal = 'support' | 'sales' | 'both'
export type Tone = 'warm' | 'professional' | 'playful' | 'calm'
export type OnboardingStep = 0 | 1 | 2 | 3

export interface OnboardingDraft {
  workspace: string
  website: string
  platform: string
  description: string
  audience: string
  goal: Goal[]
  languages: string[]
  channels: string[]
  agentName: string
  tone: Tone
  instructions: string
  replyMode: ReplyMode
  name: string
  email: string
  password: string
}

export type FieldErrors = Partial<Record<keyof OnboardingDraft, string>>

export const platformNames: Record<string, string> = {
  shopify: 'Shopify', woocommerce: 'WooCommerce', custom: 'My own website', social: 'Social media / marketplace', starting: 'Still getting started',
}
export const languageNames: Record<string, string> = { en: 'English', hi: 'Hindi', hinglish: 'Hinglish' }
export const channelNames: Record<string, string> = { website: 'Website chat', whatsapp: 'WhatsApp', email: 'Email', voice: 'Phone calls' }
export const goalNames: Record<Goal, string> = { support: 'Customer support', sales: 'Sales enquiries', both: 'Sales & support' }
export const toneNames: Record<Tone, string> = { warm: 'Warm & friendly', professional: 'Professional', playful: 'Playful', calm: 'Calm & reassuring' }
export const toneInstructions: Record<Tone, string> = {
  warm: 'Be warm, friendly and helpful. Use simple, natural words and respect the customer’s time.',
  professional: 'Be professional, clear and concise. Explain the next step without jargon or unnecessary familiarity.',
  playful: 'Be upbeat and conversational. Use a little personality for everyday questions, but be serious when someone is upset.',
  calm: 'Be calm, patient and reassuring. Acknowledge the concern and explain the next step without overpromising.',
}

export function newOnboardingDraft(): OnboardingDraft {
  return {
    workspace: '', website: '', platform: '', description: '', audience: '', goal: ['both'],
    languages: ['en', 'hi', 'hinglish'], channels: ['website'], agentName: 'Sahay', tone: 'warm',
    instructions: '', replyMode: 'draft', name: '', email: '', password: '',
  }
}

export function normalizeWebsite(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const url = new URL(/^[a-z][a-z\d+.-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`)
  if (!['https:', 'http:'].includes(url.protocol) || !url.hostname.includes('.') || url.username || url.password) {
    throw new Error('Enter a website like yourstore.in, or leave this blank.')
  }
  return url.toString()
}

export function validateOnboardingStep(draft: OnboardingDraft, step: OnboardingStep): FieldErrors {
  const errors: FieldErrors = {}
  if (step === 0) {
    if (draft.workspace.trim().length < 2) errors.workspace = 'Enter your business name.'
    if (!platformNames[draft.platform]) errors.platform = 'Choose where your business sells.'
    if (draft.description.trim().length < 3) errors.description = 'Tell Sahay what you sell or offer.'
    try { normalizeWebsite(draft.website) } catch { errors.website = 'Enter a website like yourstore.in, or leave this blank.' }
  }
  if (step === 1) {
    if (!draft.languages.length || draft.languages.some((value) => !languageNames[value])) errors.languages = 'Choose at least one language.'
    if (!draft.channels.length || draft.channels.some((value) => !channelNames[value])) errors.channels = 'Choose at least one channel.'
    if (!draft.goal.length || draft.goal.some((value) => !goalNames[value])) errors.goal = 'Choose at least one thing your agent should focus on.'
  }
  if (step === 2) {
    if (!draft.agentName.trim()) errors.agentName = 'Give your agent a name.'
    if (!toneInstructions[draft.tone]) errors.tone = 'Choose your agent’s tone.'
    if (!['draft', 'auto'].includes(draft.replyMode)) errors.replyMode = 'Choose how replies should be handled.'
  }
  if (step === 3) {
    if (draft.name.trim().length < 2) errors.name = 'Enter your name.'
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.email.trim())) errors.email = 'Enter a valid email address.'
    if (draft.password.length < 8) errors.password = 'Use at least 8 characters for your password.'
  }
  return errors
}

export function buildOnboardingPayloads(draft: OnboardingDraft) {
  const website = normalizeWebsite(draft.website)
  const businessDescription = [
    draft.description.trim(),
    `Store platform: ${platformNames[draft.platform]}.`,
    draft.audience.trim() ? `Customers: ${draft.audience.trim()}.` : '',
    `Agent focus: ${draft.goal.map((value) => goalNames[value]).join(', ')}.`,
    `Planned customer channels: ${draft.channels.map((channel) => channelNames[channel]).join(', ')}.`,
  ].filter(Boolean).join('\n')
  const voiceLanguage = draft.languages.includes('hinglish') ? 'hinglish' : draft.languages.includes('hi') ? 'hi' : 'en'
  return {
    account: {
      name: draft.name.trim(), email: draft.email.trim().toLowerCase(), password: draft.password,
      workspace: draft.workspace.trim(), website_url: website, business_description: businessDescription,
    },
    settings: {
      website_url: website, business_description: businessDescription, languages: draft.languages,
      brand_voice: toneInstructions[draft.tone], default_autonomy: draft.replyMode, brand_color: '#226192',
      widget_greeting: `Hi, I’m ${draft.agentName.trim()} from ${draft.workspace.trim()}. How can I help?`,
    },
    studio: {
      agent_name: draft.agentName.trim(),
      persona: [toneInstructions[draft.tone], draft.instructions.trim()].filter(Boolean).join('\n'),
      sales: { icp: draft.audience.trim() },
      voice: { language: voiceLanguage },
    },
  }
}

export function agentGreeting(draft: OnboardingDraft): string {
  const name = draft.agentName.trim() || 'Sahay'
  const business = draft.workspace.trim() || 'your business'
  if (draft.languages.includes('hinglish')) return `Hi! Main ${name}, ${business} se. Bataiye, aapki kaise madad karun?`
  if (draft.languages.includes('hi')) return `नमस्ते! मैं ${name}, ${business} से। आपकी कैसे मदद करूँ?`
  return `Hi! I’m ${name} from ${business}. How can I help you today?`
}
