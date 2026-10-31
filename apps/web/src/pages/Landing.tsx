import { ArrowUpRight, Check, ChevronDown, Globe2, Mail, Phone, Send, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { auth } from '../lib/api'
import './Landing.css'

type Language = 'English' | 'Hinglish'
type Scenario = 'An order update' | 'A product question' | 'A refund request'

const examples: Record<Scenario, { customer: Record<Language, string>; reply: Record<Language, string>; action: string; detail: string }> = {
  'An order update': {
    customer: { English: 'Hey, where’s my order? It’s a birthday gift!', Hinglish: 'Hi, mera order kab aayega? Birthday gift hai!' },
    reply: {
      English: 'Hi Priya! Your charm set is on its way and due tomorrow. I’ve found your tracking details, so you can keep an eye on your gift.',
      Hinglish: 'Hi Priya! Aapka charm set raste mein hai, kal tak aa jayega. Tracking details mil gayi hain, aap apne gift ka update dekh sakti hain.',
    },
    action: 'Order found. Context remembered.', detail: 'Checks the order before answering.',
  },
  'A product question': {
    customer: { English: 'I need a little birthday gift. Anything under ₹700?', Hinglish: 'Birthday ke liye cute sa gift chahiye. ₹700 ke andar kuch hai?' },
    reply: {
      English: 'The Celestial Charm Set is ₹649 and in stock. It’s a lovely little gift for someone who likes stars and moons. Would you like the product details?',
      Hinglish: 'Celestial Charm Set ₹649 ka hai aur stock mein hai. Stars aur moons pasand hain toh pyaara sa gift rahega. Product details dikhaun?',
    },
    action: 'A recommendation from the catalogue.', detail: 'Uses product prices and availability.',
  },
  'A refund request': {
    customer: { English: 'My charm set arrived broken. Can I get a refund?', Hinglish: 'Mera charm set toota hua aaya hai. Refund mil sakta hai?' },
    reply: {
      English: 'I’m sorry it arrived that way, Priya. I’ve sent your refund request to the team for approval. We’ll update you once they’ve reviewed it.',
      Hinglish: 'Priya, toota hua aaya, uske liye sorry. Refund request team ko approval ke liye bhej di hai. Review hote hi aapko update denge.',
    },
    action: 'Refund held for owner approval.', detail: 'Follows Loop Loot’s refund policy.',
  },
}

const questions = [
  { q: 'How does Sahay learn my business?', a: 'Add your store’s FAQs, shipping information, and return policies, then connect your catalogue and orders. Set your agent’s tone and action rules in Agent Studio. Sahay uses that information to answer customers.' },
  { q: 'Will I still control refunds?', a: 'Yes. You decide which actions can happen automatically and which need approval. You can review a request, edit a reply, or take over a sensitive conversation.' },
  { q: 'Where can customers reach it?', a: 'Through your website chat, Telegram, email, and connected voice provider. Each channel needs its own connection, while the customer’s history stays together in Sahay.' },
  { q: 'Does it speak my customers’ language?', a: 'Sahay is designed for English, Hindi, and Hinglish. It uses the conversation’s language and your business’s tone, so a customer can ask the way they naturally would.' },
]

function SahayMark({ small = false }: { small?: boolean }) {
  return <span className={`sahay-wordmark${small ? ' sahay-wordmark--small' : ''}`}>
    <svg viewBox="0 0 36 36" aria-hidden="true" fill="none"><path d="M7 18a11 11 0 0 1 22 0v3a7 7 0 0 1-7 7h-3l-6 4v-5A11 11 0 0 1 7 18Z" fill="currentColor" /><path d="M14 17c1.5 3.2 6.5 3.2 8 0" stroke="var(--sahay-ground)" strokeWidth="2.4" strokeLinecap="round" /></svg>
    <span>sahay<span className="sahay-wordmark-dot">.</span></span>
  </span>
}

function useDissolve() {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (!root.current || reduced.matches || CSS.supports('animation-timeline: view()') || !('IntersectionObserver' in window)) return
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.animate([
            { opacity: 0.35, transform: 'translateY(18px)' },
            { opacity: 1, transform: 'translateY(0)' },
          ], { duration: 760, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'none', delay: Number((entry.target as HTMLElement).dataset.delay || 0) })
          observer.unobserve(entry.target)
        }
      }
    }, { threshold: 0.12 })
    root.current.querySelectorAll('[data-dissolve]').forEach((element) => observer.observe(element))
    return () => observer.disconnect()
  }, [])
  return root
}

export default function Landing() {
  const root = useDissolve()
  const [language, setLanguage] = useState<Language>('Hinglish')
  const [scenario, setScenario] = useState<Scenario>('An order update')
  const example = examples[scenario]
  const me = auth.get()?.token ? auth.get() : null  // still signed in: the landing page is just another page
  const initials = (me?.user?.name || '').split(/\s+/).filter(Boolean).slice(0, 2).map((w: string) => w[0]).join('').toUpperCase() || 'S'

  useEffect(() => {
    const original = document.title
    document.title = 'Sahay — Every hello, handled.'
    return () => { document.title = original }
  }, [])

  return (
    <div className="sahay-landing" ref={root}>
      <a className="sahay-skip" href="#main-content">Skip to content</a>
      <header className="sahay-header sahay-frame">
        <a href="#" aria-label="Sahay home"><SahayMark /></a>
        <nav aria-label="Main navigation">
          <a href="#why-sahay">Why Sahay</a>
          <a href="#see-it-work">See it work</a>
          <a href="#questions">Questions</a>
        </nav>
        {me
          ? <Link className="sahay-user" to="/inbox" aria-label={`Back to your dashboard, signed in as ${me.user?.name || 'you'}`}><span aria-hidden="true">{initials}</span>{me.user?.name || 'Dashboard'}</Link>
          : <Link className="sahay-sign-in" to="/login">Sign in</Link>}
      </header>

      <main id="main-content" tabIndex={-1}>
        <section className="sahay-hero sahay-frame" aria-labelledby="hero-heading">
          <div className="sahay-hero-copy">
            <h1 id="hero-heading">Every hello,<br />handled.</h1>
            <p className="sahay-hero-description">AI sales and support for your store, from the first product question to the order that needs a little care.</p>
            <div className="sahay-hero-actions">
              <Link className="sahay-button" to={me ? '/inbox' : '/signup'}>{me ? 'Back to your dashboard' : 'Create workspace'} <ArrowUpRight size={19} aria-hidden="true" /></Link>
              <a className="sahay-text-link" href="#see-it-work">See Sahay in action</a>
            </div>
            <p className="sahay-hero-note">Your voice. Your rules. A little more breathing room.</p>
          </div>
          <div className="sahay-hero-art" data-dissolve>
            <div className="sahay-art-field">
              <img className="sahay-hero-image" src="/images/sahay-hello.webp" alt="A coral telephone receiver and its coiled cord wrapped around ivory parcels" width="1254" height="1254" fetchPriority="high" />
            </div>
            <div className="sahay-hello-note" aria-hidden="true"><span className="sahay-hello-dot" /><span>From “hello” to “happy to help.”</span></div>
          </div>
          <div className="sahay-channels" data-dissolve>
            <p>One teammate, wherever<br className="sahay-desktop-break" /> your customers are.</p>
            <ul aria-label="Supported channels">
              <li><Send aria-hidden="true" />Telegram</li>
              <li><Mail aria-hidden="true" />Email</li>
              <li><Globe2 aria-hidden="true" />Website</li>
              <li><Phone aria-hidden="true" />Voice</li>
            </ul>
          </div>
        </section>

        <section className="sahay-showcase sahay-frame" id="see-it-work" aria-labelledby="showcase-heading">
          <div className="sahay-showcase-copy">
            <h2 id="showcase-heading" data-dissolve>A little more<br />understanding.</h2>
            <p data-dissolve data-delay="70">Your customers have their own way of saying things. Sahay picks up the context, speaks their language, and finds the next step.</p>
            <div className="sahay-scenarios" aria-label="Choose an example conversation" data-dissolve data-delay="140">
              {(Object.keys(examples) as Scenario[]).map((item) => <button key={item} type="button" aria-pressed={scenario === item} onClick={() => setScenario(item)}>
                <span>{item}</span><span className="sahay-scenario-check" aria-hidden="true">{scenario === item ? <Check size={17} /> : <span />}</span>
              </button>)}
            </div>
          </div>
          <div className="sahay-demo" data-ui data-dissolve data-delay="100">
            <div className="sahay-demo-top"><SahayMark small /><span>Example conversation</span></div>
            <div className="sahay-language" role="group" aria-label="Example language">
              {(['English', 'Hinglish'] as Language[]).map((item) => <button key={item} type="button" aria-pressed={language === item} onClick={() => setLanguage(item)}>{item}</button>)}
            </div>
            <div className="sahay-conversation" key={`${scenario}-${language}`} aria-live="polite" aria-atomic="true">
              <div className="sahay-customer-message"><span>Priya</span><p>{example.customer[language]}</p></div>
              <div className="sahay-agent-message"><span>Sahay</span><p>{example.reply[language]}</p></div>
              <div className="sahay-action-result"><Check size={16} aria-hidden="true" /><span>{example.action}</span></div>
            </div>
            <p className="sahay-demo-foot">{example.detail} An illustrative preview, not a live chat.</p>
          </div>
        </section>

        <section className="sahay-control sahay-frame" id="why-sahay" aria-labelledby="control-heading">
          <div className="sahay-control-copy">
            <h2 id="control-heading" data-dissolve>Your business.<br />Your say.</h2>
            <p data-dissolve data-delay="60">Helpful replies can run on their own. Refunds and exceptions follow the rules you set, with a person in the loop when it matters.</p>
            <ul className="sahay-promises">
              <li data-dissolve data-delay="100"><Check aria-hidden="true" /> Answers grounded in your store’s knowledge</li>
              <li data-dissolve data-delay="160"><Check aria-hidden="true" /> One customer history across conversations</li>
              <li data-dissolve data-delay="220"><Check aria-hidden="true" /> A human handoff when it needs your touch</li>
            </ul>
          </div>
          <div className="sahay-control-evidence">
            <div className="sahay-policy-fragment" data-ui data-dissolve>
              <div className="sahay-fragment-title"><ShieldCheck size={20} aria-hidden="true" /><span>Loop Loot’s rule</span><span>Example</span></div>
              <p>“Refunds always need my approval.”</p>
              <div className="sahay-rule-condition"><span>Applies to</span><span>Every refund request</span></div>
              <div className="sahay-rule-state"><span>Refund request</span><span>Owner approval</span></div>
            </div>
            <div className="sahay-approval-fragment" data-ui data-dissolve data-delay="100">
              <div className="sahay-fragment-title"><span>Ready for your review</span><span>Example</span></div>
              <p>Priya’s charm set arrived damaged.</p>
              <div className="sahay-approval-order"><span>Order #LL1042</span><span>Celestial Charm Set</span></div>
              <div className="sahay-approval-state"><span>Refund request <strong>₹649</strong></span><span><span className="sahay-pending-dot" />Awaiting approval</span></div>
            </div>
          </div>
        </section>

        <section className="sahay-faq sahay-frame" id="questions" aria-labelledby="faq-heading">
          <h2 id="faq-heading" data-dissolve>A few good<br />questions.</h2>
          <div className="sahay-questions">
            {questions.map(({ q, a }, i) => <details key={q} data-dissolve data-delay={i * 60}>
              <summary>{q}<ChevronDown size={19} aria-hidden="true" /></summary>
              <p>{a}</p>
            </details>)}
          </div>
        </section>
      </main>

      <footer className="sahay-footer sahay-frame">
        <div className="sahay-footer-top">
          <h2 data-dissolve>Make room for<br />your business.</h2>
          <div data-dissolve data-delay="80"><p>Let Sahay take the next conversation.</p><Link className="sahay-button sahay-button--ivory" to={me ? '/inbox' : '/signup'}>{me ? 'Back to your dashboard' : 'Create workspace'}</Link></div>
        </div>
        <div className="sahay-footer-bottom"><a href="#" aria-label="Back to Sahay home"><SahayMark /></a><p>Good conversations. Growing businesses.</p><span>© {new Date().getFullYear()} Sahay</span></div>
      </footer>
    </div>
  )
}
