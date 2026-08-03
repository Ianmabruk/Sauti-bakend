import { motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { api } from '../../services/api'

const fadeUp = {
  hidden: { opacity: 0, y: 20 },
  show: (i = 0) => ({ opacity: 1, y: 0, transition: { duration: 0.7, delay: i * 0.1, ease: [0.4, 0, 0.2, 1] } }),
}

export const AboutPage = () => {
  const [about, setAbout] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/content/about')
      .then((res) => setAbout(res.data))
      .catch(() => setAbout(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="section-pad container-wide px-6 md:px-12 lg:px-20">
        <div className="skeleton h-12 w-48 mb-8" />
        <div className="grid gap-16 md:grid-cols-2">
          <div className="skeleton aspect-[4/5] w-full" />
          <div className="space-y-4">
            {[1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-4 w-full" />)}
          </div>
        </div>
      </div>
    )
  }

  if (!about) {
    return (
      <div className="section-pad container-narrow px-6 text-center">
        <h1 className="font-display text-6xl font-medium">About</h1>
        <p className="mt-6 text-sm text-ink/40">About content has not been configured yet.</p>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="section-pad bg-linen pb-0">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <motion.div initial="hidden" animate="show" variants={fadeUp}>
            <p className="eyebrow mb-4">Our Story</p>
            <h1 className="font-display text-6xl font-medium leading-tight text-ink md:text-8xl">
              About HOK
            </h1>
          </motion.div>
        </div>
      </div>

      {/* Hero image */}
      {about.aboutImageUrl && (
        <motion.div
          initial={{ opacity: 0, scale: 1.02 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.9 }}
          className="mt-12"
        >
          <img
            src={about.aboutImageUrl}
            alt="About HOK Interior Designs"
            className="h-[55vh] w-full object-cover bg-linen md:h-[70vh]"
            loading="lazy"
          />
        </motion.div>
      )}

      {/* Story + Values */}
      <section className="section-pad bg-cream">
        <div className="container-wide px-6 md:px-12 lg:px-20">
          <div className="grid gap-16 md:grid-cols-2">
            <motion.div
              initial="hidden"
              whileInView="show"
              viewport={{ once: true }}
              variants={fadeUp}
              custom={0}
            >
              {about.story && (
                <>
                  <p className="eyebrow mb-4">Our Story</p>
                  <p className="text-lg leading-relaxed text-ink/60">{about.story}</p>
                </>
              )}
              {about.companyDescription && (
                <p className="mt-4 text-base leading-relaxed text-ink/55">{about.companyDescription}</p>
              )}
            </motion.div>

            <motion.div
              initial="hidden"
              whileInView="show"
              viewport={{ once: true }}
              variants={fadeUp}
              custom={1}
              className="space-y-6"
            >
              {about.mission && (
                <div className="border-l-2 border-clay pl-6">
                  <p className="eyebrow mb-2">Mission</p>
                  <p className="text-base leading-relaxed text-ink/60">{about.mission}</p>
                </div>
              )}
              {about.vision && (
                <div className="border-l-2 border-sand pl-6">
                  <p className="eyebrow mb-2">Vision</p>
                  <p className="text-base leading-relaxed text-ink/60">{about.vision}</p>
                </div>
              )}
              {about.location && (
                <div className="border-l-2 border-sand pl-6">
                  <p className="eyebrow mb-2">Location</p>
                  <p className="text-base text-ink/60">{about.location}</p>
                </div>
              )}
              {about.contactEmail && (
                <div className="border-l-2 border-sand pl-6">
                  <p className="eyebrow mb-2">Contact</p>
                  <a href={`mailto:${about.contactEmail}`} className="text-base text-ink/60 transition hover:text-ink">
                    {about.contactEmail}
                  </a>
                </div>
              )}
            </motion.div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="bg-ink py-20">
        <div className="container-narrow px-6 text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.7 }}
          >
            <p className="eyebrow text-white/40 mb-4">Ready to Begin?</p>
            <h2 className="font-display text-5xl font-medium text-white md:text-6xl">
              Let's Create Something Beautiful
            </h2>
            <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
              <Link to="/portfolio" className="btn-outline border-white/30 text-white hover:bg-white hover:text-ink hover:border-white">
                View Our Work <ArrowRight size={14} strokeWidth={1.5} />
              </Link>
              <Link to="/virtual-interior-design" className="btn-ghost text-white/50 hover:text-white">
                Virtual Showroom
              </Link>
            </div>
          </motion.div>
        </div>
      </section>
    </div>
  )
}
