import { motion, AnimatePresence } from 'framer-motion'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { X, ArrowRight } from 'lucide-react'
import { api } from '../../services/api'

const getFirstMedia = (project) => {
  if (Array.isArray(project.media) && project.media.length) return project.media[0]
  if (project.videoUrl) return { type: 'video', url: project.videoUrl }
  if (project.coverImageUrl) return { type: 'image', url: project.coverImageUrl }
  return null
}

export const ProjectsPage = () => {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    api.get('/content/projects')
      .then((res) => setProjects(res.data || []))
      .catch(() => setProjects([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (selected) document.body.style.overflow = 'hidden'
    else document.body.style.overflow = ''
    return () => { document.body.style.overflow = '' }
  }, [selected])

  return (
    <div>
      {/* Header */}
      <div className="section-pad bg-linen pb-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}>
            <p className="eyebrow mb-4">Design Work</p>
            <h1 className="font-display text-6xl font-medium leading-tight text-ink md:text-7xl">Projects</h1>
            <p className="mt-4 max-w-xl text-base text-ink/50">
              Interior design projects — videos and images from our portfolio.
            </p>
          </motion.div>
        </div>
      </div>

      {/* Grid */}
      <div className="section-pad bg-cream pt-12">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          {loading && (
            <div className="grid gap-6 md:grid-cols-2">
              {[1, 2, 3, 4].map((i) => (
                <div key={i}>
                  <div className="skeleton aspect-[16/10] w-full" />
                  <div className="mt-4 space-y-2">
                    <div className="skeleton h-6 w-48" />
                    <div className="skeleton h-4 w-full" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loading && projects.length === 0 && (
            <div className="py-24 text-center">
              <p className="font-display text-3xl text-ink/30">No projects published yet.</p>
            </div>
          )}

          <div className="grid gap-10 md:grid-cols-2">
            {projects.map((project, i) => {
              const firstMedia = getFirstMedia(project)
              return (
                <motion.article
                  key={project._id}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.07, duration: 0.6 }}
                  className="group cursor-pointer"
                  onClick={() => setSelected(project)}
                >
                  <div className="relative overflow-hidden bg-linen aspect-[16/10]">
                    {firstMedia?.type === 'video' ? (
                      <video
                        src={firstMedia.url}
                        className="h-full w-full object-cover transition duration-700 group-hover:scale-105"
                        autoPlay muted loop playsInline preload="metadata"
                      />
                    ) : firstMedia?.type === 'image' ? (
                      <img
                        src={firstMedia.url}
                        alt={project.title}
                        className="h-full w-full object-cover transition duration-700 group-hover:scale-105"
                        loading="lazy"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center bg-linen">
                        <p className="text-sm text-ink/30">No media</p>
                      </div>
                    )}
                    <div className="absolute inset-0 bg-ink/0 transition-all duration-500 group-hover:bg-ink/15" />
                    {Array.isArray(project.media) && project.media.length > 1 && (
                      <span className="absolute right-4 top-4 bg-white/90 px-3 py-1 text-2xs font-medium uppercase tracking-widest text-ink">
                        {project.media.length} items
                      </span>
                    )}
                  </div>
                  <div className="pt-5">
                    <h2 className="font-display text-3xl font-medium text-ink transition-colors group-hover:text-warm">
                      {project.title}
                    </h2>
                    <p className="mt-2 text-sm leading-relaxed text-ink/50 line-clamp-2">{project.description}</p>
                    <span className="mt-4 inline-flex items-center gap-2 text-2xs font-medium uppercase tracking-widest text-ink/40 transition group-hover:text-ink">
                      View Project <ArrowRight size={12} strokeWidth={1.5} />
                    </span>
                  </div>
                </motion.article>
              )
            })}
          </div>

          <div className="mt-16 text-center">
            <Link to="/portfolio" className="btn-outline">
              View Full Portfolio <ArrowRight size={14} strokeWidth={1.5} />
            </Link>
          </div>
        </div>
      </div>

      {/* Modal */}
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
              className="relative max-h-[90vh] w-full max-w-5xl overflow-hidden bg-white"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => setSelected(null)}
                className="absolute right-4 top-4 z-10 flex h-9 w-9 items-center justify-center bg-white/90 text-ink transition hover:bg-white"
                aria-label="Close"
              >
                <X size={16} strokeWidth={1.5} />
              </button>
              <div className="max-h-[70vh] overflow-y-auto">
                {(selected.media?.length
                  ? selected.media
                  : [getFirstMedia(selected)].filter(Boolean)
                ).map((m, idx) =>
                  m.type === 'video' ? (
                    <video key={idx} src={m.url} controls autoPlay muted loop playsInline className="w-full" />
                  ) : (
                    <img key={idx} src={m.url} alt={selected.title} className="w-full object-contain" />
                  ),
                )}
              </div>
              <div className="flex items-center justify-between p-6">
                <div>
                  <h2 className="font-display text-3xl font-medium">{selected.title}</h2>
                  {selected.description && <p className="mt-1 text-sm text-ink/55">{selected.description}</p>}
                </div>
                <button onClick={() => setSelected(null)} className="btn-ghost text-ink/40">Close</button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
