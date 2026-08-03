import { AnimatePresence, motion } from 'framer-motion'
import { Maximize2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../services/api'

export const VirtualDesignPage = () => {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [fullscreen, setFullscreen] = useState(null)

  useEffect(() => {
    api.get('/content/virtual-design')
      .then((res) => setItems(res.data || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (fullscreen) document.body.style.overflow = 'hidden'
    else document.body.style.overflow = ''
    return () => { document.body.style.overflow = '' }
  }, [fullscreen])

  const filtered = useMemo(() => {
    if (!query) return items
    const q = query.toLowerCase()
    return items.filter((i) => i.title?.toLowerCase().includes(q) || i.description?.toLowerCase().includes(q))
  }, [items, query])

  return (
    <div>
      {/* Header */}
      <div className="section-pad bg-linen pb-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}>
            <p className="eyebrow mb-4">Immersive Design</p>
            <h1 className="font-display text-6xl font-medium leading-tight text-ink md:text-7xl">
              Virtual Showroom
            </h1>
            <p className="mt-4 max-w-xl text-base text-ink/50">
              Immersive walkthroughs, luxury showcases, and interior design experiences.
            </p>
          </motion.div>
        </div>
      </div>

      {/* Search */}
      <div className="border-b border-sand bg-cream">
        <div className="container-wide px-6 py-4 md:px-12 lg:px-20">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search showroom..."
            className="input-box max-w-xs py-2 text-xs"
          />
        </div>
      </div>

      {/* Grid */}
      <div className="section-pad bg-cream pt-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          {loading && (
            <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
              {[1, 2, 3].map((i) => (
                <div key={i}>
                  <div className="skeleton aspect-[4/3] w-full" />
                  <div className="mt-4 space-y-2">
                    <div className="skeleton h-5 w-48" />
                    <div className="skeleton h-4 w-full" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loading && filtered.length === 0 && (
            <div className="py-24 text-center">
              <p className="font-display text-3xl text-ink/30">
                {items.length === 0 ? 'No showroom content yet.' : 'No results found.'}
              </p>
            </div>
          )}

          <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((item, i) => (
              <motion.article
                key={item._id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.07, duration: 0.6 }}
                className="group"
              >
                <div className="relative overflow-hidden bg-linen aspect-[4/3]">
                  <video
                    src={item.videoUrl}
                    className="h-full w-full object-cover transition duration-700 group-hover:scale-105"
                    autoPlay loop muted playsInline preload="metadata"
                  />
                  <div className="absolute inset-0 bg-ink/0 transition-all duration-500 group-hover:bg-ink/20" />
                  <button
                    onClick={() => setFullscreen(item)}
                    className="absolute right-4 top-4 flex h-10 w-10 items-center justify-center bg-white/90 text-ink opacity-0 transition-all duration-300 group-hover:opacity-100 hover:bg-white"
                    aria-label="Full screen"
                  >
                    <Maximize2 size={15} strokeWidth={1.5} />
                  </button>
                </div>
                <div className="pt-5">
                  <h3 className="font-display text-2xl font-medium text-ink">{item.title}</h3>
                  {item.description && (
                    <p className="mt-2 text-sm leading-relaxed text-ink/50 line-clamp-2">{item.description}</p>
                  )}
                  {item.services?.length > 0 && (
                    <div className="mt-4 flex flex-wrap gap-2">
                      {item.services.map((s, idx) => (
                        <span key={idx} className="border border-sand px-3 py-1 text-2xs font-medium uppercase tracking-widest text-ink/50">
                          {s.title}
                        </span>
                      ))}
                    </div>
                  )}
                  <button
                    onClick={() => setFullscreen(item)}
                    className="mt-5 btn-outline py-2.5 px-6 text-2xs"
                  >
                    Watch Full Screen
                  </button>
                </div>
              </motion.article>
            ))}
          </div>
        </div>
      </div>

      {/* Fullscreen modal */}
      <AnimatePresence>
        {fullscreen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-ink"
          >
            <button
              onClick={() => setFullscreen(null)}
              className="absolute right-5 top-5 z-10 flex h-11 w-11 items-center justify-center border border-white/20 text-white/70 transition hover:border-white hover:text-white"
              aria-label="Close"
            >
              <X size={18} strokeWidth={1.5} />
            </button>
            <video
              src={fullscreen.videoUrl}
              controls autoPlay loop playsInline
              className="max-h-screen w-full object-contain"
            />
            <div className="absolute bottom-8 left-0 right-0 px-8 text-center">
              <p className="font-display text-2xl font-medium text-white/80">{fullscreen.title}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
