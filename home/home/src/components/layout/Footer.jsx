import { Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { SHOP_CATEGORIES } from '../../utils/constants'
import { NewsletterForm } from '../common/NewsletterForm'
import { api } from '../../services/api'
import { FaInstagram, FaTiktok, FaPinterestP, FaFacebookF } from 'react-icons/fa6'

const SOCIAL_LINKS = [
  { key: 'instagram', Icon: FaInstagram, label: 'Instagram' },
  { key: 'tiktok', Icon: FaTiktok, label: 'TikTok' },
  { key: 'pinterest', Icon: FaPinterestP, label: 'Pinterest' },
  { key: 'facebook', Icon: FaFacebookF, label: 'Facebook' },
]

export const Footer = () => {
  const [about, setAbout] = useState(null)

  useEffect(() => {
    api.get('/content/about').then((res) => setAbout(res.data)).catch(() => setAbout(null))
  }, [])

  const socials = about?.socials || {}

  return (
    <footer className="bg-ink text-white">
      {/* Newsletter band */}
      <div className="border-b border-white/10 bg-charcoal">
        <div className="container-wide flex flex-col items-center gap-6 px-6 py-12 text-center md:flex-row md:justify-between md:text-left md:px-12 lg:px-20">
          <div>
            <p className="text-2xs font-medium uppercase tracking-widest text-clay mb-2">Stay Inspired</p>
            <h3 className="font-display text-3xl font-medium text-white md:text-4xl">
              Design Notes & Curated Drops
            </h3>
          </div>
          <div className="w-full max-w-sm">
            <NewsletterForm />
          </div>
        </div>
      </div>

      {/* Main footer grid */}
      <div className="container-wide grid gap-12 px-6 py-16 md:grid-cols-4 md:px-12 lg:px-20">
        {/* Brand */}
        <div>
          <Link to="/">
            <p className="font-display text-3xl font-semibold text-white">HOK</p>
            <p className="text-2xs font-medium uppercase tracking-widest text-clay">Interior Designs</p>
          </Link>
          <p className="mt-5 text-sm leading-relaxed text-white/45">
            Crafting spaces that inspire — from concept to completion.
          </p>
          {/* Social Icons */}
          <div className="mt-6 flex items-center gap-3">
            {SOCIAL_LINKS.map(({ key, Icon, label }) =>
              socials[key] ? (
                <a
                  key={key}
                  href={socials[key]}
                  target="_blank"
                  rel="noreferrer noopener"
                  aria-label={label}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/15 bg-white/5 text-white/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-clay hover:bg-clay/20 hover:text-clay hover:shadow-md"
                >
                  <Icon size={15} />
                </a>
              ) : null
            )}
            {/* Show placeholder icons when no socials configured */}
            {!Object.values(socials).some(Boolean) &&
              SOCIAL_LINKS.map(({ key, Icon, label }) => (
                <span
                  key={key}
                  aria-label={label}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/10 text-white/20"
                >
                  <Icon size={15} />
                </span>
              ))}
          </div>
        </div>

        {/* Explore */}
        <div>
          <p className="text-2xs font-medium uppercase tracking-widest text-white/35 mb-5">Explore</p>
          <ul className="space-y-3 text-sm text-white/55">
            {[
              { to: '/shop', label: 'Shop' },
              { to: '/portfolio', label: 'Portfolio' },
              { to: '/virtual-interior-design', label: 'Virtual Interior Design' },
              { to: '/about', label: 'About Us' },
            ].map((link) => (
              <li key={link.to}>
                <Link to={link.to} className="transition-colors hover:text-white">
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>

        {/* Shop Categories */}
        <div>
          <p className="text-2xs font-medium uppercase tracking-widest text-white/35 mb-5">Shop</p>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm text-white/55">
            {SHOP_CATEGORIES.slice(0, 10).map((category) => (
              <li key={category}>
                <Link
                  to={`/shop?category=${encodeURIComponent(category)}`}
                  className="transition-colors hover:text-white"
                >
                  {category}
                </Link>
              </li>
            ))}
          </ul>
        </div>

        {/* Contact */}
        <div>
          <p className="text-2xs font-medium uppercase tracking-widest text-white/35 mb-5">Contact</p>
          <div className="space-y-3 text-sm text-white/55">
            {about?.location && (
              <p className="flex items-start gap-2">
                <span className="mt-0.5 text-clay">↟</span>
                {about.location}
              </p>
            )}
            {about?.contactEmail && (
              <p>
                <a href={`mailto:${about.contactEmail}`} className="transition-colors hover:text-white">
                  {about.contactEmail}
                </a>
              </p>
            )}
            {!about?.location && !about?.contactEmail && (
              <p className="text-white/25">Contact info not configured</p>
            )}
          </div>
          <div className="mt-8">
            <Link to="/about" className="inline-flex items-center gap-2 border border-white/20 px-5 py-2.5 text-2xs font-medium uppercase tracking-widest text-white/60 transition hover:border-white/60 hover:text-white">
              Get In Touch
            </Link>
          </div>
        </div>
      </div>

      {/* Bottom bar */}
      <div className="border-t border-white/8 px-6 py-5 md:px-12 lg:px-20">
        <div className="container-wide flex flex-col items-center justify-between gap-3 text-2xs text-white/30 md:flex-row">
          <p>© {new Date().getFullYear()} HOK Interior Designs. All rights reserved.</p>
          <div className="flex gap-6">
            <Link to="/about" className="hover:text-white/60 transition-colors">Privacy</Link>
            <Link to="/about" className="hover:text-white/60 transition-colors">Terms</Link>
          </div>
        </div>
      </div>
    </footer>
  )
}
