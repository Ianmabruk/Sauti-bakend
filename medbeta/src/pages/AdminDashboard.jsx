import React, { useEffect, useState } from "react";
import { Shield, Users, UserPlus } from "lucide-react";

export default function AdminDashboard() {
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(false);

  async function fetchPending() {
    setLoading(true);
    try {
      const res = await fetch("http://localhost:5000/users?role=doctor&status=pending");
      const data = await res.json();
      setPending(data);
    } catch {
      setPending([]);
    }
    setLoading(false);
  }

  useEffect(() => {
    fetchPending();
  }, []);

  async function approveDoctor(id) {
    if (!window.confirm("Approve this doctor and send credentials via email?")) return;

    const res = await fetch(`http://localhost:5000/users/${id}/approve`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ adminName: "Admin", loginUrl: "http://localhost:3000/auth" }),
    });
    const data = await res.json();
    if (data.success) {
      alert("Doctor approved and email sent (if email config is correct).");
      fetchPending();
    } else {
      alert("Could not approve: " + (data.error || JSON.stringify(data)));
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 flex text-slate-900">
      {/* Sidebar */}
      <aside className="hidden lg:flex flex-col w-64 bg-white border-r border-slate-200 fixed inset-y-0 left-0 z-30">
        <div className="h-16 flex items-center gap-3 px-6 border-b border-slate-200">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-slate-700 to-slate-900 flex items-center justify-center">
            <Shield className="w-5 h-5 text-white" />
          </div>
          <span className="text-lg font-bold text-slate-900 tracking-tight">Admin Portal</span>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          <a href="#" className="sidebar-link active">
            <Shield className="w-4 h-4" />
            Dashboard
          </a>
          <a href="#" className="sidebar-link">
            <Users className="w-4 h-4" />
            Staff
          </a>
        </nav>

        <div className="p-4 border-t border-slate-200">
          <a href="/portal-select" className="sidebar-link text-red-600 hover:text-red-700">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" /></svg>
            Logout
          </a>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 lg:ml-64">
        <header className="sticky top-0 z-20 glass border-b border-slate-200/60">
          <div className="h-16 px-6 flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold text-slate-900">Hospital Administration</h1>
              <p className="text-xs text-slate-500">Manage staff, approvals, and system settings</p>
            </div>
          </div>
        </header>

        <main className="p-6">
          <div className="page-header">
            <h1>Admin Dashboard</h1>
            <p>Register new doctors and approve pending accounts</p>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="card rounded-2xl p-6">
              <div className="flex items-center gap-2 mb-5">
                <div className="w-8 h-8 rounded-lg bg-med-100 flex items-center justify-center">
                  <UserPlus className="w-4 h-4 text-med-600" />
                </div>
                <h3 className="font-semibold text-slate-900">Register Doctor</h3>
              </div>
              <RegisterDoctorForm onCreated={() => fetchPending()} />
            </div>

            <div className="card rounded-2xl p-6">
              <div className="flex items-center gap-2 mb-5">
                <div className="w-8 h-8 rounded-lg bg-amber-100 flex items-center justify-center">
                  <Users className="w-4 h-4 text-amber-600" />
                </div>
                <h3 className="font-semibold text-slate-900">Pending Doctors</h3>
              </div>
              {loading ? (
                <div className="text-sm text-slate-500 py-8 text-center">Loading...</div>
              ) : pending.length === 0 ? (
                <div className="text-sm text-slate-500 py-8 text-center">No pending doctors</div>
              ) : (
                <div className="space-y-3">
                  {pending.map((d) => (
                    <div key={d.id} className="flex items-center justify-between p-4 rounded-xl border border-slate-200 bg-white">
                      <div>
                        <div className="font-medium text-sm text-slate-900">{d.name}</div>
                        <div className="text-xs text-slate-500 mt-0.5">{d.email} {d.meta?.department ? `• ${d.meta.department}` : ""}</div>
                      </div>
                      <button
                        onClick={() => approveDoctor(d.id)}
                        className="px-4 py-2 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-700 transition-colors"
                      >
                        Approve
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
