import { BedDouble, BriefcaseBusiness, Building2, Lamp, Sparkles, Sofa, TreePalm, UtensilsCrossed, SlidersHorizontal, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ProductCard } from '../../components/shop/ProductCard'
import { api } from '../../services/api'
import { SHOP_CATEGORIES } from '../../utils/constants'

const categoryIcons = {
  'Living Room': Sofa,
  Kitchen: UtensilsCrossed,
  Bedroom: BedDouble,
  Dining: UtensilsCrossed,
  Outdoor: TreePalm,
  Commercial: Building2,
  Decor: Sparkles,
  Lighting: Lamp,
  Office: BriefcaseBusiness,
  'Custom Designs': Sparkles,
}

export const ShopPage = () => {
  const [allProducts, setAllProducts] = useState([])
  const [loading, setLoading] = useState(true)
  const [category, setCategory] = useState('')
  const [query, setQuery] = useState('')
  const [minPrice, setMinPrice] = useState('')
  const [maxPrice, setMaxPrice] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)

  useEffect(() => {
    api.get('/products', { params: { sort: '-createdAt', limit: 100 } })
      .then((res) => setAllProducts(res.data.items || []))
      .catch(() => setAllProducts([]))
      .finally(() => setLoading(false))
  }, [])

  const products = useMemo(() => {
    let next = [...allProducts]
    if (query) {
      const q = query.toLowerCase()
      next = next.filter((item) => [item.name, item.description, item.category, item.sku].join(' ').toLowerCase().includes(q))
    }
    if (category) next = next.filter((item) => item.category === category)
    if (minPrice) next = next.filter((item) => (item.discountPrice || item.price) >= Number(minPrice))
    if (maxPrice) next = next.filter((item) => (item.discountPrice || item.price) <= Number(maxPrice))
    return next.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
  }, [allProducts, category, query, minPrice, maxPrice])

  const hasFilters = category || query || minPrice || maxPrice
  const clearFilters = () => { setCategory(''); setQuery(''); setMinPrice(''); setMaxPrice('') }

  return (
    <div>
      {/* Header */}
      <div className="section-pad bg-linen pb-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}>
            <p className="eyebrow mb-4">Curated Collection</p>
            <h1 className="font-display text-6xl font-medium leading-tight text-ink md:text-7xl">Shop</h1>
          </motion.div>
        </div>
      </div>

      {/* Category strip */}
      <div className="border-b border-sand bg-cream">
        <div className="container-wide overflow-x-auto px-6 md:px-12 lg:px-20">
          <div className="flex items-center gap-1 py-4 min-w-max">
            <button
              onClick={() => setCategory('')}
              className={`px-5 py-2 text-2xs font-medium uppercase tracking-widest transition ${
                !category ? 'bg-ink text-white' : 'text-ink/50 hover:text-ink'
              }`}
            >
              All
            </button>
            {SHOP_CATEGORIES.map((cat) => {
              const Icon = categoryIcons[cat] || Sparkles
              return (
                <button
                  key={cat}
                  onClick={() => setCategory(cat === category ? '' : cat)}
                  className={`flex items-center gap-1.5 px-5 py-2 text-2xs font-medium uppercase tracking-widest transition ${
                    category === cat ? 'bg-ink text-white' : 'text-ink/50 hover:text-ink'
                  }`}
                >
                  <Icon size={12} strokeWidth={1.5} />
                  {cat}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {/* Search + filter bar */}
      <div className="sticky top-[88px] z-30 border-b border-sand bg-cream/95 backdrop-blur-sm md:top-[108px]">
        <div className="container-wide px-6 py-3 md:px-12 lg:px-20">
          <div className="flex items-center gap-3">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search products..."
              className="input-box flex-1 max-w-xs py-2 text-xs"
            />
            <button
              onClick={() => setFiltersOpen((p) => !p)}
              className="flex items-center gap-2 border border-sand px-4 py-2 text-2xs font-medium uppercase tracking-widest text-ink/50 transition hover:border-ink/40 hover:text-ink"
            >
              <SlidersHorizontal size={13} strokeWidth={1.5} />
              Filters
            </button>
            {hasFilters && (
              <button
                onClick={clearFilters}
                className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-widest text-ink/40 transition hover:text-ink"
              >
                <X size={12} strokeWidth={1.5} /> Clear
              </button>
            )}
            <span className="ml-auto text-2xs text-ink/35">{products.length} items</span>
          </div>

          {filtersOpen && (
            <div className="flex flex-wrap gap-4 py-3 border-t border-sand mt-3">
              <div>
                <label className="label">Min Price</label>
                <input value={minPrice} onChange={(e) => setMinPrice(e.target.value)} placeholder="$0" className="input-box w-28 py-2 text-xs" />
              </div>
              <div>
                <label className="label">Max Price</label>
                <input value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} placeholder="Any" className="input-box w-28 py-2 text-xs" />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Products grid */}
      <div className="section-pad bg-cream pt-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          {loading && (
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                <div key={i}>
                  <div className="skeleton aspect-[3/4] w-full" />
                  <div className="mt-3 space-y-2">
                    <div className="skeleton h-3 w-20" />
                    <div className="skeleton h-5 w-40" />
                    <div className="skeleton h-4 w-16" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loading && products.length === 0 && (
            <div className="py-24 text-center">
              <p className="font-display text-3xl text-ink/30">No products found</p>
              <p className="mt-2 text-sm text-ink/35">Try adjusting your filters</p>
              {hasFilters && (
                <button onClick={clearFilters} className="mt-6 btn-outline">Clear Filters</button>
              )}
            </div>
          )}

          <div className="grid gap-x-6 gap-y-12 sm:grid-cols-2 lg:grid-cols-4">
            {products.map((product, i) => (
              <motion.div
                key={product._id}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: Math.min(i * 0.05, 0.4) }}
              >
                <ProductCard product={product} />
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
