import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ChevronLeft, ChevronRight } from 'lucide-react'
import { SectionTitle } from '../../components/common/SectionTitle'
import { ProductCard } from '../../components/shop/ProductCard'
import { api } from '../../services/api'

const HERO_INTERVAL = 7000

export const HomePage = () => {
  const [feed, setFeed] = useState({
    projects: [],
    portfolio: [],
    about: null,
    virtualDesign: [],
    products: [],
  })
  const [loading, setLoading] = useState(true)
  const [heroIndex, setHeroIndex] = useState(0)
  const timerRef = useRef(null)

  const loadFeed = useCallback(() => {
    api
      .get('/content/homepage')
      .then((res) => setFeed(res.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadFeed() }, [loadFeed])

  // Build hero media list — videos first, then images, newest project first
  const heroMedia = useMemo(() => {
    const items = []
    const sorted = [...feed.projects].sort(
      (a, b) => new Date(b.createdAt || 0) - new Date(a.createdAt || 0),
    )
    sorted.forEach((p) => {
      if (Array.isArray(p.media) && p.media.length) {
        p.media.forEach((m) =>
          items.push({ ...m, title: p.title, description: p.description }),
        )
        return
      }
      if (p.videoUrl)
        items.push({ type: 'video', url: p.videoUrl, title: p.title, description: p.description })
      else if (p.coverImageUrl)
        items.push({ type: 'image', url: p.coverImageUrl, title: p.title, description: p.description })
    })
    return items
  }, [feed.projects])

  const startTimer = useCallback(() => {
    window.clearInterval(timerRef.current)
    if (heroMedia.length < 2) return
    timerRef.current = window.setInterval(
      () => setHeroIndex((i) => (i + 1) % heroMedia.length),
      HERO_INTERVAL,
    )
  }, [heroMedia.length])

  useEffect(() => {
    startTimer()
    return () => window.clearInterval(timerRef.current)
  }, [startTimer])

  const goTo = (index) => {
    setHeroIndex(index)
    startTimer()
  }

  const newest = useMemo(
    () =>
      [...feed.products]
        .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
        .slice(0, 4),
    [feed.products],
  )

  const portfolioGrid = feed.portfolio.slice(0, 6)
  const current = heroMedia[heroIndex]

  if (loading) {
    return (
      <div className="-mt-[88px] md:-mt-[108px]">
        <div className="skeleton h-screen w-full" />
        <div className="section-pad container-wide grid gap-4 md:grid-cols-3">
          {[1, 2, 3].map((i) => <div key={i} className="skeleton h-80" />)}
        </div>
      </div>
    )
  }

  return (
    <div className="-mt-[88px] md:-mt-[108px]">

      {/* ══════════════════════════════════════════
          SECTION 1 — PROJECT VIDEO HERO
          Full-width cinematic, autoplay, newest first
      ══════════════════════════════════════════ */}
      <section className="relative h-screen min-h-[600px] w-full overflow-hidden bg-ink">
        <AnimatePresence mode="wait">
          {heroMedia.length > 0 ? (
            <motion.div
              key={`${current?.url}-${heroIndex}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 1.2, ease: 'easeInOut' }}
              className="absolute inset-0"
            >
              {current?.type === 'video' ? (
                <video
                  src={current.url}
                  autoPlay
                  muted
                  loop
                  playsInline
                  preload="auto"
                  className="h-full w-full object-cover"
                />
              ) : (
                <img
                  src={current?.url}
                  alt={current?.title || 'HOK Interior Design'}
                  className="h-full w-full object-cover"
                  loading="eager"
                />
              )}
              {/* Cinematic gradient overlay */}
              <div className="absolute inset-0 bg-gradient-to-b from-ink/25 via-transparent to-ink/75" />
            </motion.div>
          ) : (
            /* Fallback when no projects uploaded yet */
            <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-charcoal via-ink to-charcoal">
              <div className="text-center">
                <p className="font-display text-8xl font-medium text-white/10 md:text-[12rem]">HOK</p>
                <p className="absolute inset-0 flex items-center justify-center font-display text-6xl font-medium text-white md:text-8xl">
                  HOK
                </p>
              </div>
            </div>
          )}
        </AnimatePresence>

        {/* Hero text — bottom left, editorial */}
        <div className="absolute inset-0 flex flex-col justify-end pb-20 md:pb-28">
          <div className="container-wide px-6 md:px-12 lg:px-20">
            <AnimatePresence mode="wait">
              <motion.div
                key={`text-${heroIndex}`}
                initial={{ opacity: 0, y: 24 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -12 }}
                transition={{ duration: 0.7, delay: 0.2, ease: [0.4, 0, 0.2, 1] }}
                className="max-w-2xl"
              >
                {heroMedia.length > 0 && current?.title ? (
                  <>
                    <p className="mb-3 text-2xs font-medium uppercase tracking-widest text-white/50">
                      Featured Project
                    </p>
                    <h1 className="font-display text-5xl font-medium leading-tight text-white drop-shadow-lg md:text-7xl">
                      {current.title}
                    </h1>
                    {current.description && (
                      <p className="mt-4 max-w-lg text-sm leading-relaxed text-white/60 md:text-base">
                        {current.description}
                      </p>
                    )}
                  </>
                ) : (
                  <>
                    <p className="mb-3 text-2xs font-medium uppercase tracking-widest text-white/50">
                      HOK Interior Designs
                    </p>
                    <h1 className="font-display text-5xl font-medium leading-tight text-white md:text-7xl">
                      Crafting Spaces<br />That Inspire
                    </h1>
                  </>
                )}
                <div className="mt-8 flex flex-wrap gap-4">
                  <Link
                    to="/portfolio"
                    className="inline-flex items-center gap-2 border border-white/40 px-7 py-3.5 text-2xs font-medium uppercase tracking-widest text-white transition-all hover:bg-white hover:text-ink"
                  >
                    View Portfolio <ArrowRight size={13} strokeWidth={1.5} />
                  </Link>
                  <Link
                    to="/about"
                    className="inline-flex items-center gap-2 px-7 py-3.5 text-2xs font-medium uppercase tracking-widest text-white/60 transition-all hover:text-white"
                  >
                    About Us
                  </Link>
                </div>
              </motion.div>
            </AnimatePresence>
          </div>
        </div>

        {/* Slide counter + arrows */}
        {heroMedia.length > 1 && (
          <div className="absolute bottom-8 right-6 z-10 flex items-center gap-3 md:right-12 lg:right-20">
            <button
              onClick={() => goTo((heroIndex - 1 + heroMedia.length) % heroMedia.length)}
              className="flex h-10 w-10 items-center justify-center border border-white/25 text-white/60 transition hover:border-white hover:text-white"
              aria-label="Previous slide"
            >
              <ChevronLeft size={16} strokeWidth={1.5} />
            </button>
            <span className="min-w-[3rem] text-center text-2xs font-medium text-white/40">
              {String(heroIndex + 1).padStart(2, '0')} / {String(heroMedia.length).padStart(2, '0')}
            </span>
            <button
              onClick={() => goTo((heroIndex + 1) % heroMedia.length)}
              className="flex h-10 w-10 items-center justify-center border border-white/25 text-white/60 transition hover:border-white hover:text-white"
              aria-label="Next slide"
            >
              <ChevronRight size={16} strokeWidth={1.5} />
            </button>
          </div>
        )}

        {/* Scroll cue */}
        <motion.div
          animate={{ y: [0, 8, 0] }}
          transition={{ repeat: Infinity, duration: 2, ease: 'easeInOut' }}
          className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2"
        >
          <span className="text-2xs font-medium uppercase tracking-widest text-white/30">Scroll</span>
          <div className="h-8 w-px bg-gradient-to-b from-white/30 to-transparent" />
        </motion.div>
      </section>

      {/* ══════════════════════════════════════════
          SECTION 2 — PORTFOLIO PREVIEW
          Latest 6 images, masonry grid
      ══════════════════════════════════════════ */}
      <section className="section-pad bg-cream">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <div className="mb-12 flex items-end justify-between">
            <SectionTitle eyebrow="Portfolio" title="Curated Interiors" align="left" />
            <Link
              to="/portfolio"
              className="hidden items-center gap-2 text-2xs font-medium uppercase tracking-widest text-ink/45 transition hover:text-ink md:inline-flex"
            >
              View Full Portfolio <ArrowRight size={13} strokeWidth={1.5} />
            </Link>
          </div>

          {portfolioGrid.length > 0 ? (
            <>
              {/* Asymmetric editorial grid */}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4">
                {portfolioGrid.map((item, i) => (
                  <motion.div
                    key={item._id}
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true, margin: '-60px' }}
                    transition={{ duration: 0.6, delay: i * 0.07 }}
                    className={`group relative overflow-hidden bg-linen ${
                      i === 0 ? 'col-span-2 md:col-span-1 md:row-span-2' : ''
                    }`}
                  >
                    <img
                      src={item.imageUrl}
                      alt={item.title}
                      className={`w-full object-cover transition duration-700 group-hover:scale-105 ${
                        i === 0 ? 'h-[320px] md:h-full md:min-h-[500px]' : 'h-52 md:h-60'
                      }`}
                      loading="lazy"
                    />
                    <div className="absolute inset-0 bg-ink/0 transition-all duration-500 group-hover:bg-ink/20" />
                    <div className="absolute bottom-0 left-0 right-0 translate-y-full p-4 transition-transform duration-400 group-hover:translate-y-0">
                      <p className="font-display text-xl font-medium text-white drop-shadow">
                        {item.title}
                      </p>
                      {item.category && (
                        <p className="text-2xs font-medium uppercase tracking-widest text-white/65">
                          {item.category}
                        </p>
                      )}
                    </div>
                  </motion.div>
                ))}
              </div>

              <div className="mt-10 text-center">
                <Link to="/portfolio" className="btn-outline">
                  View Full Portfolio <ArrowRight size={14} strokeWidth={1.5} />
                </Link>
              </div>
            </>
          ) : (
            <div className="py-16 text-center">
              <p className="font-display text-3xl text-ink/20">No portfolio items yet</p>
              <p className="mt-2 text-sm text-ink/35">Upload images from the Admin Dashboard</p>
            </div>
          )}
        </div>
      </section>

      {/* ══════════════════════════════════════════
          SECTION 3 — ABOUT PREVIEW
          Image left, text right, from admin
      ══════════════════════════════════════════ */}
      {feed.about && (
        <section className="section-pad bg-linen">
          <div className="container-wide px-6 md:px-12 lg:px-20">
            <div className="grid items-center gap-16 md:grid-cols-2">
              {/* Image */}
              <motion.div
                initial={{ opacity: 0, x: -24 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true, margin: '-80px' }}
                transition={{ duration: 0.8 }}
                className="relative"
              >
                {feed.about.aboutImageUrl ? (
                  <img
                    src={feed.about.aboutImageUrl}
                    alt="About HOK Interior Designs"
                    className="aspect-[4/5] w-full object-cover bg-sand"
                    loading="lazy"
                  />
                ) : (
                  <div className="aspect-[4/5] w-full bg-sand flex items-center justify-center">
                    <p className="text-sm text-ink/30">No image uploaded</p>
                  </div>
                )}
                {/* Decorative offset frame */}
                <div className="absolute -bottom-4 -right-4 -z-10 h-full w-full border border-sand" />
              </motion.div>

              {/* Text */}
              <motion.div
                initial={{ opacity: 0, x: 24 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true, margin: '-80px' }}
                transition={{ duration: 0.8, delay: 0.15 }}
              >
                <p className="eyebrow mb-4">About HOK</p>
                <h2 className="font-display text-5xl font-medium leading-tight text-ink md:text-6xl">
                  {feed.about.vision || 'Crafting Spaces That Inspire'}
                </h2>
                <p className="mt-6 text-base leading-relaxed text-ink/55">
                  {feed.about.story || feed.about.companyDescription}
                </p>
                {feed.about.mission && (
                  <div className="mt-6 border-l-2 border-clay pl-5">
                    <p className="text-sm leading-relaxed text-ink/55">{feed.about.mission}</p>
                  </div>
                )}
                <div className="mt-10 flex flex-wrap gap-4">
                  <Link to="/about" className="btn-primary">
                    Learn More <ArrowRight size={14} strokeWidth={1.5} />
                  </Link>
                  <Link to="/portfolio" className="btn-outline">
                    Our Work
                  </Link>
                </div>
              </motion.div>
            </div>
          </div>
        </section>
      )}

      {/* ══════════════════════════════════════════
          SERVICES STRIP
      ══════════════════════════════════════════ */}
      <section className="bg-ink py-16">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <div className="grid gap-8 md:grid-cols-3">
            {[
              {
                num: '01',
                title: 'Interior Design',
                desc: 'Full-service residential and commercial design from concept to completion.',
                to: '/about',
              },
              {
                num: '02',
                title: 'Virtual Showroom',
                desc: 'Immersive 3D walkthroughs and virtual staging for any space.',
                to: '/virtual-interior-design',
              },
              {
                num: '03',
                title: 'Curated Shop',
                desc: 'Handpicked furniture, lighting, and décor from premium makers.',
                to: '/shop',
              },
            ].map((s, i) => (
              <motion.div
                key={s.num}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.6, delay: i * 0.1 }}
                className="group border-t border-white/10 pt-8"
              >
                <p className="font-display text-5xl font-medium text-white/10">{s.num}</p>
                <h3 className="mt-4 font-display text-2xl font-medium text-white">{s.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-white/40">{s.desc}</p>
                <Link
                  to={s.to}
                  className="mt-5 inline-flex items-center gap-2 text-2xs font-medium uppercase tracking-widest text-white/30 transition group-hover:text-white/70"
                >
                  Explore <ArrowRight size={12} strokeWidth={1.5} />
                </Link>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════
          NEW ARRIVALS
      ══════════════════════════════════════════ */}
      {newest.length > 0 && (
        <section className="section-pad bg-cream">
          <div className="container-wide px-6 md:px-12 lg:px-20">
            <div className="mb-12 flex items-end justify-between">
              <SectionTitle eyebrow="Shop" title="New Arrivals" align="left" />
              <Link
                to="/shop"
                className="hidden items-center gap-2 text-2xs font-medium uppercase tracking-widest text-ink/45 transition hover:text-ink md:inline-flex"
              >
                Shop All <ArrowRight size={13} strokeWidth={1.5} />
              </Link>
            </div>
            <div className="grid gap-x-6 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
              {newest.map((product, i) => (
                <motion.div
                  key={product._id}
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.5, delay: i * 0.08 }}
                >
                  <ProductCard product={product} />
                </motion.div>
              ))}
            </div>
            <div className="mt-10 text-center md:hidden">
              <Link to="/shop" className="btn-outline">Shop All</Link>
            </div>
          </div>
        </section>
      )}

      {/* ══════════════════════════════════════════
          QUOTE BAND
      ══════════════════════════════════════════ */}
      <section className="bg-linen py-20">
        <div className="container-narrow px-6 text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
          >
            <p className="font-display text-3xl font-medium italic leading-relaxed text-ink/60 md:text-4xl">
              "Good design is not about what you add — it's about what you leave out."
            </p>
            <p className="mt-6 eyebrow text-ink/30">HOK Interior Designs</p>
          </motion.div>
        </div>
      </section>
    </div>
  )
}
