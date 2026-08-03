import { Heart, Menu, MessageCircle, ShoppingBag, User, X, ChevronDown } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { useShop } from '../../context/ShopContext'

const NAV_ITEMS = [
  { to: '/shop', label: 'Shop' },
  { to: '/portfolio', label: 'Portfolio' },
  { to: '/virtual-interior-design', label: 'Virtual Interior Design' },
  { to: '/about', label: 'About' },
]

export const Navbar = () => {
  const [menuOpen, setMenuOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const { user, logout } = useAuth()
  const { wishlist, cart } = useShop()
  const location = useLocation()

  // Close mobile menu and profile dropdown on route change
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMenuOpen(false)
    setProfileOpen(false)
  }, [location.pathname])

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 50)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    document.body.style.overflow = menuOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [menuOpen])

  // Close profile dropdown on outside click
  useEffect(() => {
    if (!profileOpen) return
    const handler = (e) => {
      if (!e.target.closest('[data-profile-menu]')) setProfileOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [profileOpen])

  return (
    <header
      className={`fixed left-0 right-0 top-0 z-50 transition-all duration-500 ${
        scrolled ? 'bg-cream/98 shadow-soft backdrop-blur-sm' : 'bg-cream/95 backdrop-blur-sm'
      }`}
    >
      {/* Announcement bar */}
      <div className="hidden border-b border-sand/50 bg-linen py-2 text-center text-2xs font-medium uppercase tracking-widest text-ink/45 md:block">
        Complimentary design consultation — Book yours today
      </div>

      <div className="container-wide flex items-center justify-between px-6 py-4 md:px-12 lg:px-20">
        {/* Logo */}
        <Link to="/" className="flex-shrink-0 leading-tight">
          <p className="font-display text-2xl font-semibold tracking-wide text-ink md:text-3xl">HOK</p>
          <p className="text-2xs font-medium uppercase tracking-widest text-warm">Interior Designs</p>
        </Link>

        {/* Desktop Nav */}
        <nav className="hidden items-center gap-8 md:flex lg:gap-10">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `relative text-2xs font-medium uppercase tracking-widest transition-colors duration-200 after:absolute after:-bottom-0.5 after:left-0 after:h-px after:bg-ink after:transition-all after:duration-300 ${
                  isActive
                    ? 'text-ink after:w-full'
                    : 'text-ink/50 hover:text-ink after:w-0 hover:after:w-full'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Desktop Actions */}
        <div className="hidden items-center gap-0.5 md:flex">
          {/* Wishlist */}
          <Link
            to="/wishlist"
            className="relative p-2.5 text-ink/55 transition-colors hover:text-ink"
            aria-label="Wishlist"
          >
            <Heart size={17} strokeWidth={1.5} />
            {wishlist.length > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-ink text-[9px] font-semibold text-white">
                {wishlist.length > 9 ? '9+' : wishlist.length}
              </span>
            )}
          </Link>

          {/* Cart */}
          <Link
            to="/cart"
            className="relative p-2.5 text-ink/55 transition-colors hover:text-ink"
            aria-label="Cart"
          >
            <ShoppingBag size={17} strokeWidth={1.5} />
            {cart.length > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-ink text-[9px] font-semibold text-white">
                {cart.length > 9 ? '9+' : cart.length}
              </span>
            )}
          </Link>

          {/* Chat */}
          <Link to="/chat" className="p-2.5 text-ink/55 transition-colors hover:text-ink" aria-label="Chat">
            <MessageCircle size={17} strokeWidth={1.5} />
          </Link>

          {/* Profile */}
          <div className="relative ml-1" data-profile-menu>
            <button
              className="flex items-center gap-1 p-2.5 text-ink/55 transition-colors hover:text-ink"
              onClick={() => setProfileOpen((p) => !p)}
              aria-label="Account"
              aria-expanded={profileOpen}
            >
              <User size={17} strokeWidth={1.5} />
              <ChevronDown
                size={11}
                strokeWidth={2}
                className={`transition-transform duration-200 ${profileOpen ? 'rotate-180' : ''}`}
              />
            </button>

            <AnimatePresence>
              {profileOpen && (
                <motion.div
                  initial={{ opacity: 0, y: 6, scale: 0.98 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 4, scale: 0.98 }}
                  transition={{ duration: 0.15 }}
                  className="absolute right-0 mt-1 w-52 border border-sand bg-white py-1 shadow-lift"
                >
                  {!user ? (
                    <>
                      <Link to="/login" className="block px-5 py-3 text-xs font-medium uppercase tracking-wider text-ink/65 transition hover:bg-linen hover:text-ink">
                        Sign In
                      </Link>
                      <Link to="/register" className="block px-5 py-3 text-xs font-medium uppercase tracking-wider text-ink/65 transition hover:bg-linen hover:text-ink">
                        Sign Up
                      </Link>
                    </>
                  ) : (
                    <>
                      <div className="border-b border-sand px-5 py-3">
                        <p className="text-xs font-medium text-ink">{user.fullName || user.name}</p>
                        <p className="text-2xs text-ink/40">{user.email}</p>
                      </div>
                      <Link to="/account" className="block px-5 py-3 text-xs font-medium uppercase tracking-wider text-ink/65 transition hover:bg-linen hover:text-ink">
                        Account
                      </Link>
                      {user.role === 'admin' && (
                        <Link to="/admin" className="block px-5 py-3 text-xs font-medium uppercase tracking-wider text-ink/65 transition hover:bg-linen hover:text-ink">
                          Admin Dashboard
                        </Link>
                      )}
                      <button
                        onClick={logout}
                        className="w-full border-t border-sand px-5 py-3 text-left text-xs font-medium uppercase tracking-wider text-ink/65 transition hover:bg-linen hover:text-ink"
                      >
                        Sign Out
                      </button>
                    </>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>

        {/* Mobile: icons + hamburger */}
        <div className="flex items-center gap-1 md:hidden">
          <Link to="/wishlist" className="relative p-2 text-ink/55" aria-label="Wishlist">
            <Heart size={18} strokeWidth={1.5} />
            {wishlist.length > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-ink text-[9px] font-semibold text-white">
                {wishlist.length}
              </span>
            )}
          </Link>
          <Link to="/cart" className="relative p-2 text-ink/55" aria-label="Cart">
            <ShoppingBag size={18} strokeWidth={1.5} />
            {cart.length > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-ink text-[9px] font-semibold text-white">
                {cart.length}
              </span>
            )}
          </Link>
          <button
            className="p-2 text-ink"
            onClick={() => setMenuOpen((p) => !p)}
            aria-label="Toggle menu"
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={22} strokeWidth={1.5} /> : <Menu size={22} strokeWidth={1.5} />}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      <AnimatePresence>
        {menuOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden border-t border-sand bg-cream md:hidden"
          >
            <nav className="flex flex-col px-6 py-6">
              {NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `border-b border-sand/60 py-4 text-sm font-medium uppercase tracking-widest transition-colors ${
                      isActive ? 'text-ink' : 'text-ink/50 hover:text-ink'
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
              <Link to="/chat" className="border-b border-sand/60 py-4 text-sm font-medium uppercase tracking-widest text-ink/50 hover:text-ink transition-colors">
                Chat
              </Link>

              <div className="mt-6 flex gap-3">
                {!user ? (
                  <>
                    <Link to="/login" className="btn-outline flex-1 py-3 text-2xs">Sign In</Link>
                    <Link to="/register" className="btn-primary flex-1 py-3 text-2xs">Sign Up</Link>
                  </>
                ) : (
                  <>
                    <Link to="/account" className="btn-outline flex-1 py-3 text-2xs">Account</Link>
                    <button onClick={logout} className="btn-primary flex-1 py-3 text-2xs">Sign Out</button>
                  </>
                )}
              </div>
              {user?.role === 'admin' && (
                <Link to="/admin" className="mt-3 text-center text-2xs font-medium uppercase tracking-widest text-warm hover:text-ink transition-colors">
                  Admin Dashboard
                </Link>
              )}
            </nav>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  )
}
