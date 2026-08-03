import React from "react";
import { motion as Motion } from "framer-motion";
import { Link } from "react-router-dom";
import { Lock, Zap, FileText, Shield, Activity, Users } from "lucide-react";

export default function Landing() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-med-50 font-sans antialiased">
      {/* Top Nav */}
      <header className="fixed top-0 left-0 right-0 z-50 glass border-b border-white/20">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-med-500 to-med-700 flex items-center justify-center">
              <Activity className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-slate-900 tracking-tight">MedBeta</span>
          </div>

          <nav className="hidden md:flex items-center gap-8">
            <a href="#home" className="text-sm font-medium text-slate-600 hover:text-med-600 transition-colors">Home</a>
            <a href="#features" className="text-sm font-medium text-slate-600 hover:text-med-600 transition-colors">Features</a>
            <a href="#about" className="text-sm font-medium text-slate-600 hover:text-med-600 transition-colors">About</a>
            <Link to="/portal-select" className="text-sm font-medium text-med-600 hover:text-med-700 transition-colors">Get Started</Link>
          </nav>
        </div>
      </header>

      {/* Hero Section */}
      <section id="home" className="relative pt-32 pb-20 lg:pt-40 lg:pb-32 overflow-hidden">
        <div className="absolute inset-0 -z-10">
          <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full h-full bg-gradient-to-b from-med-100/40 via-transparent to-transparent" />
          <div className="absolute top-20 left-10 w-72 h-72 bg-med-200/30 rounded-full blur-3xl animate-blob" />
          <div className="absolute top-40 right-10 w-72 h-72 bg-blue-200/30 rounded-full blur-3xl animate-blob animation-delay-2000" />
        </div>

        <div className="max-w-7xl mx-auto px-6">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-8 items-center">
            <Motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
              className="max-w-xl"
            >
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-med-50 border border-med-200 text-med-700 text-xs font-semibold mb-6">
                <Shield className="w-3.5 h-3.5" />
                HIPAA Compliant
              </div>

              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-slate-900 mb-6 leading-[1.1] tracking-tight">
                Smart, Secure & Real-Time{" "}
                <span className="bg-gradient-to-r from-med-600 to-blue-600 bg-clip-text text-transparent">Health Records</span>
              </h1>

              <p className="text-lg text-slate-600 mb-8 leading-relaxed max-w-lg">
                Access patient medical history securely from any healthcare facility, ensuring better continuity of care and faster clinical decisions.
              </p>

              <div className="flex flex-wrap items-center gap-4">
                <Link to="/portal-select">
                  <button className="btn-primary px-8 py-3.5 text-base">
                    Get Started
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" /></svg>
                  </button>
                </Link>
                <a href="#features" className="btn-secondary px-6 py-3.5 text-base">
                  Learn More
                </a>
              </div>

              <div className="flex items-center gap-6 mt-8 pt-8 border-t border-slate-200/80">
                <div className="text-center">
                  <div className="text-2xl font-bold text-slate-900">50+</div>
                  <div className="text-xs text-slate-500 font-medium">Hospitals</div>
                </div>
                <div className="w-px h-10 bg-slate-200" />
                <div className="text-center">
                  <div className="text-2xl font-bold text-slate-900">10k+</div>
                  <div className="text-xs text-slate-500 font-medium">Patients</div>
                </div>
                <div className="w-px h-10 bg-slate-200" />
                <div className="text-center">
                  <div className="text-2xl font-bold text-slate-900">99.9%</div>
                  <div className="text-xs text-slate-500 font-medium">Uptime</div>
                </div>
              </div>
            </Motion.div>

            <Motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.8, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
              className="relative lg:pl-8"
            >
              <div className="relative">
                <div className="absolute -inset-4 bg-gradient-to-r from-med-500/20 to-blue-500/20 rounded-3xl blur-2xl" />
                <img
                  src="/doctor-illustration.png"
                  alt="Medical illustration"
                  className="relative w-full max-w-md mx-auto drop-shadow-2xl rounded-3xl border border-white/60"
                />
              </div>

              {/* Floating Cards */}
              <Motion.div
                animate={{ y: [0, -10, 0] }}
                transition={{ repeat: Infinity, duration: 4, ease: "easeInOut" }}
                className="absolute -left-4 top-1/4 glass rounded-2xl p-4 shadow-lg hidden lg:block"
              >
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-green-100 flex items-center justify-center">
                    <Shield className="w-5 h-5 text-green-600" />
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-slate-900">Encrypted</div>
                    <div className="text-[11px] text-slate-500">End-to-end secured</div>
                  </div>
                </div>
              </Motion.div>

              <Motion.div
                animate={{ y: [0, 10, 0] }}
                transition={{ repeat: Infinity, duration: 5, ease: "easeInOut", delay: 1 }}
                className="absolute -right-4 bottom-1/4 glass rounded-2xl p-4 shadow-lg hidden lg:block"
              >
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-med-100 flex items-center justify-center">
                    <Activity className="w-5 h-5 text-med-600" />
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-slate-900">Real-time</div>
                    <div className="text-[11px] text-slate-500">Live sync active</div>
                  </div>
                </div>
              </Motion.div>
            </Motion.div>
          </div>
        </div>
      </section>

      {/* Feature Highlights */}
      <section id="features" className="py-20 bg-white/50">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-4 tracking-tight">Built for Modern Healthcare</h2>
            <p className="text-slate-600 max-w-2xl mx-auto">Everything you need to manage patient records, appointments, and clinical workflows in one unified platform.</p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[
              { icon: Lock, title: "Secure Cloud Storage", desc: "End-to-end encryption for patient data with HIPAA compliance.", color: "med" },
              { icon: Zap, title: "Real-Time Updates", desc: "Instantly sync new diagnoses, treatments, and lab results across facilities.", color: "amber" },
              { icon: FileText, title: "Cross-Hospital Access", desc: "View and share records from any connected hospital in the network.", color: "emerald" },
              { icon: Shield, title: "Role-Based Access", desc: "Granular permissions for doctors, patients, labs, pharmacists, and admins.", color: "violet" },
              { icon: Activity, title: "Smart Diagnostics", desc: "AI-assisted insights from patient history to support clinical decisions.", color: "rose" },
              { icon: Users, title: "Team Collaboration", desc: "Seamless handoff between doctors, labs, and pharmacists.", color: "teal" },
            ].map((feature, i) => (
              <Motion.div
                key={i}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1 }}
                className="card rounded-2xl p-6 group"
              >
                <div className={`w-12 h-12 rounded-xl bg-${feature.color}-100 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform`}>
                  <feature.icon className={`w-6 h-6 text-${feature.color}-600`} />
                </div>
                <h3 className="text-lg font-semibold text-slate-900 mb-2">{feature.title}</h3>
                <p className="text-sm text-slate-600 leading-relaxed">{feature.desc}</p>
              </Motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section id="about" className="py-20">
        <div className="max-w-4xl mx-auto px-6">
          <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 to-slate-800 p-10 sm:p-16 text-center">
            <div className="absolute inset-0 bg-gradient-to-br from-med-600/20 to-blue-600/20" />
            <div className="relative z-10">
              <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4 tracking-tight">Ready to transform healthcare?</h2>
              <p className="text-slate-300 mb-8 max-w-xl mx-auto">Join the network of hospitals and clinics already using MedBeta to deliver better patient outcomes.</p>
              <Link to="/portal-select">
                <button className="btn-primary px-8 py-3.5 text-base">
                  Start Your Journey
                </button>
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-slate-200/80 bg-white/60 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-md bg-gradient-to-br from-med-500 to-med-700 flex items-center justify-center">
              <Activity className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-sm font-semibold text-slate-900">MedBeta</span>
          </div>
          <p className="text-sm text-slate-500">2025 MedBeta. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
