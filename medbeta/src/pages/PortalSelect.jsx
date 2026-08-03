import React from "react";
import { Link } from "react-router-dom";
import { Stethoscope, UserRound, FlaskConical, Pill, ShieldCheck } from "lucide-react";

const portals = [
  { role: "doctor", label: "Doctor", icon: Stethoscope, desc: "Manage patients, bookings, and records", color: "med" },
  { role: "patient", label: "Patient", icon: UserRound, desc: "Access your health history and book appointments", color: "emerald" },
  { role: "lab", label: "Lab Technician", icon: FlaskConical, desc: "Process test requests and upload results", color: "amber" },
  { role: "pharmacist", label: "Pharmacist", icon: Pill, desc: "Manage prescriptions and medication queue", color: "violet" },
  { role: "admin", label: "Administration", icon: ShieldCheck, desc: "Oversee staff, approvals, and system settings", color: "slate" },
];

export default function PortalSelect() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-med-50 flex flex-col">
      <header className="fixed top-0 left-0 right-0 z-50 glass border-b border-white/20">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-med-500 to-med-700 flex items-center justify-center">
              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" /></svg>
            </div>
            <span className="text-xl font-bold text-slate-900 tracking-tight">MedBeta</span>
          </div>
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center px-4 py-24">
        <div className="w-full max-w-5xl">
          <div className="text-center mb-12">
            <h1 className="text-3xl sm:text-4xl font-bold text-slate-900 mb-3 tracking-tight">Select Your Portal</h1>
            <p className="text-slate-600 max-w-lg mx-auto">Choose your role to access the appropriate dashboard and tools.</p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {portals.map((p) => (
              <Link key={p.role} to={`/auth?role=${p.role}`}>
                <div className="card rounded-2xl p-6 h-full cursor-pointer group">
                  <div className={`w-12 h-12 rounded-xl bg-${p.color}-100 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform`}>
                    <p.icon className={`w-6 h-6 text-${p.color}-600`} />
                  </div>
                  <h3 className="text-lg font-semibold text-slate-900 mb-1">{p.label}</h3>
                  <p className="text-sm text-slate-600">{p.desc}</p>
                  <div className="mt-4 flex items-center gap-1 text-sm font-medium text-med-600 opacity-0 group-hover:opacity-100 transition-opacity">
                    Enter portal
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" /></svg>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
