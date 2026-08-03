import { motion, AnimatePresence } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { X, ArrowRight } from 'lucide-react'
import { api } from '../../services/api'

const PAGE_SIZE = 12

export const PortfolioPage = () => {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [page, setPage] = useState(1)

  useEffect(() => {
    api.get('/content/portfolio')
      .then((res) => setItems(res.data || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (selected) document.body.style.overflow = 'hidden'
    else document.body.style.overflow = ''
    return () => { document.body.style.overflow = '' }
  }, [selected])

  const categories = useMemo(() => {
    const set = new Set(items.map((i) => i.category).filter(Boolean))
    return Array.from(set).sort()
  }, [items])

  const filtered = useMemo(() => {
    let next = [...items]
    if (query) {
      const q = query.toLowerCase()
      next = next.filter((i) => i.title?.toLowerCase().includes(q) || i.category?.toLowerCase().includes(q))
    }
    if (category) next = next.filter((i) => i.category === category)
    return next
  }, [items, query, category])

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const paginated = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div>
      {/* Page header */}
      <div className="section-pad bg-linen pb-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}>
            <p className="eyebrow mb-4">Our Work</p>
            <h1 className="font-display text-6xl font-medium leading-tight text-ink md:text-7xl">Portfolio</h1>
            <p className="mt-4 max-w-xl text-base text-ink/50">
              A curated view of premium interiors delivered by HOK Interior Designs.
            </p>
          </motion.div>
        </div>
      </div>

      {/* Filters */}
      <div className="sticky top-[88px] z-30 border-b border-sand bg-cream/95 backdrop-blur-sm md:top-[108px]">
        <div className="container-wide px-6 py-4 md:px-12 lg:px-20">
          <div className="flex flex-wrap items-center gap-3">
            <input
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1) }}
              placeholder="Search..."
              className="input-box max-w-xs py-2 text-xs"
            />
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => { setCategory(''); setPage(1) }}
                className={`px-4 py-2 text-2xs font-medium uppercase tracking-widest transition ${
                  !category ? 'bg-ink text-white' : 'border border-sand text-ink/55 hover:border-ink/40 hover:text-ink'
                }`}
              >
                All
              </button>
              {categories.map((cat) => (
                <button
                  key={cat}
                  onClick={() => { setCategory(cat); setPage(1) }}
                  className={`px-4 py-2 text-2xs font-medium uppercase tracking-widest transition ${
                    category === cat ? 'bg-ink text-white' : 'border border-sand text-ink/55 hover:border-ink/40 hover:text-ink'
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
            <span className="ml-auto text-2xs text-ink/35">{filtered.length} items</span>
          </div>
        </div>
      </div>

      {/* Grid */}
      <div className="section-pad bg-cream pt-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          {loading && (
            <div className="columns-1 gap-4 sm:columns-2 lg:columns-3">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <div key={i} className={`skeleton mb-4 ${i % 3 === 0 ? 'h-96' : 'h-64'}`} />
              ))}
            </div>
          )}

          {!loading && paginated.length === 0 && (
            <div className="py-24 text-center">
              <p className="font-display text-3xl text-ink/30">No items found</p>
              <p className="mt-2 text-sm text-ink/35">Try adjusting your filters</p>
            </div>
          )}

          <div className="columns-1 gap-4 sm:columns-2 lg:columns-3">
            {paginated.map((item, index) => (
              <motion.button
                key={item._id}
                onClick={() => setSelected(item)}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.04, duration: 0.5 }}
                className="group mb-4 w-full overflow-hidden bg-linen text-left"
              >
                <div className="relative overflow-hidden">
                  <img
                    src={item.imageUrl}
                    alt={item.title}
                    className="w-full object-cover transition duration-700 group-hover:scale-105"
                    loading="lazy"
                  />
                  <div className="absolute inset-0 bg-ink/0 transition-all duration-500 group-hover:bg-ink/25" />
                  <div className="absolute inset-0 flex items-end p-5 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
                    <div>
                      <p className="font-display text-2xl font-medium text-white">{item.title}</p>
                      {item.category && <p className="text-2xs font-medium uppercase tracking-widest text-white/65">{item.category}</p>}
                    </div>
                  </div>
                </div>
                <div className="p-4 md:hidden">
                  <p className="font-display text-xl font-medium">{item.title}</p>
                  {item.category && <p className="text-2xs font-medium uppercase tracking-widest text-warm">{item.category}</p>}
                </div>
              </motion.button>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-12 flex items-center justify-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="border border-sand px-5 py-2.5 text-2xs font-medium uppercase tracking-widest text-ink/50 transition hover:border-ink/40 hover:text-ink disabled:opacity-30"
              >
                Prev
              </button>
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className={`h-10 w-10 text-2xs font-medium transition ${
                    p === page ? 'bg-ink text-white' : 'border border-sand text-ink/50 hover:border-ink/40 hover:text-ink'
                  }`}
                >
                  {p}
                </button>
              ))}
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="border border-sand px-5 py-2.5 text-2xs font-medium uppercase tracking-widest text-ink/50 transition hover:border-ink/40 hover:text-ink disabled:opacity-30"
              >
                Next
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Lightbox */}
      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-ink/90 p-4 md:p-8"
            onClick={() => setSelected(null)}
          >
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="relative max-h-[90vh] max-w-4xl w-full overflow-hidden bg-white"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => setSelected(null)}
                className="absolute right-4 top-4 z-10 flex h-9 w-9 items-center justify-center bg-white/90 text-ink transition hover:bg-white"
                aria-label="Close"
              >
                <X size={16} strokeWidth={1.5} />
              </button>
              <img
                src={selected.imageUrl}
                alt={selected.title}
                className="max-h-[72vh] w-full object-contain"
              />
              <div className="flex items-center justify-between p-6">
                <div>
                  <h2 className="font-display text-3xl font-medium">{selected.title}</h2>
                  {selected.category && <p className="text-2xs font-medium uppercase tracking-widest text-warm">{selected.category}</p>}
                </div>
                <button onClick={() => setSelected(null)} className="btn-ghost text-ink/40">
                  Close <ArrowRight size={13} strokeWidth={1.5} />
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
