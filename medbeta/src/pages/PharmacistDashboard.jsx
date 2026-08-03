import React, { useState, useEffect } from "react";
import { Pill, ClipboardList, Bell, LogOut, User, Sun, Moon } from "lucide-react";

const initialPrescriptions = [
  { id: 1, patient: "Patient A", medication: "Paracetamol", doctor: "Dr. John Doe", pharmacist: "Alice", status: "Done" },
  { id: 2, patient: "Patient B", medication: "Ibuprofen", doctor: "Dr. Jane Smith", pharmacist: null, status: "Pending" },
  { id: 3, patient: "Patient C", medication: "Amoxicillin", doctor: "Dr. John Doe", pharmacist: "Bob", status: "Done" },
];

export default function PharmacistDashboard() {
  const [loggedIn, setLoggedIn] = useState(JSON.parse(localStorage.getItem("pharmaLoggedIn")) || false);
  const [nameInput, setNameInput] = useState("");
  const [profilePic, setProfilePic] = useState(null);
  const [pharmacist, setPharmacist] = useState(JSON.parse(localStorage.getItem("pharmacist")) || { name: "", profilePic: null });
  const [prescriptions, setPrescriptions] = useState(JSON.parse(localStorage.getItem("prescriptions")) || initialPrescriptions);
  const [theme, setTheme] = useState(localStorage.getItem("theme") || "blue");

  useEffect(() => localStorage.setItem("pharmacist", JSON.stringify(pharmacist)), [pharmacist]);
  useEffect(() => localStorage.setItem("pharmaLoggedIn", JSON.stringify(loggedIn)), [loggedIn]);
  useEffect(() => localStorage.setItem("prescriptions", JSON.stringify(prescriptions)), [prescriptions]);
  useEffect(() => localStorage.setItem("theme", theme), [theme]);

  const handleLogin = () => {
    if (!nameInput.trim()) return alert("Enter your name!");
    setPharmacist({ name: nameInput, profilePic });
    setLoggedIn(true);
  };

  const handleProfilePic = (e) => {
    setProfilePic(URL.createObjectURL(e.target.files[0]));
  };

  const handleLogout = () => {
    setLoggedIn(false);
    setNameInput("");
    setProfilePic(null);
    setPharmacist({ name: "", profilePic: null });
  };

  const toggleTheme = () => setTheme(theme === "blue" ? "black" : "blue");

  const markDone = (id) => {
    setPrescriptions(prev =>
      prev.map(p =>
        p.id === id ? { ...p, status: "Done", pharmacist: pharmacist.name } : p
      )
    );
  };

  if (!loggedIn) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center px-4 relative overflow-hidden">
        <div className="absolute inset-0">
          <div className="absolute top-0 right-1/4 w-96 h-96 bg-violet-600/20 rounded-full blur-3xl animate-blob" />
        </div>
        <div className="relative w-full max-w-sm">
          <div className="glass-dark rounded-3xl p-8 shadow-2xl">
            <div className="text-center mb-8">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center mx-auto mb-4">
                <Pill className="w-6 h-6 text-white" />
              </div>
              <h2 className="text-2xl font-bold text-white mb-1">Pharmacist</h2>
              <p className="text-sm text-slate-400">Sign in to manage prescriptions</p>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5 ml-1">Your Name</label>
                <input
                  type="text"
                  placeholder="Enter your name"
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  className="input-field bg-slate-900/50 border-slate-700 text-white placeholder:text-slate-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5 ml-1">Profile Picture</label>
                <label className="flex items-center justify-center gap-2 w-full py-3 rounded-xl border border-dashed border-slate-600 text-slate-400 text-sm cursor-pointer hover:border-slate-500 hover:text-slate-300 transition-colors">
                  <User className="w-4 h-4" />
                  {profilePic ? "Change picture" : "Upload picture"}
                  <input type="file" className="hidden" onChange={handleProfilePic} />
                </label>
              </div>
              <button onClick={handleLogin} className="w-full btn-primary py-3">
                Get Started
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const isDark = theme === "black";
  const pendingCount = prescriptions.filter(p => p.status === "Pending").length;

  return (
    <div className={`min-h-screen flex text-slate-900 ${isDark ? "bg-slate-900 text-slate-100" : "bg-slate-50 text-slate-900"}`}>
      {/* Sidebar */}
      <aside className="hidden lg:flex flex-col w-64 bg-white border-r border-slate-200 fixed inset-y-0 left-0 z-30">
        <div className="h-16 flex items-center gap-3 px-6 border-b border-slate-200">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center">
            <Pill className="w-5 h-5 text-white" />
          </div>
          <span className="text-lg font-bold text-slate-900 tracking-tight">Pharma Portal</span>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          <a href="#" className="sidebar-link active">
            <Pill className="w-4 h-4" />
            Prescriptions
          </a>
          <a href="#" className="sidebar-link">
            <ClipboardList className="w-4 h-4" />
            History
          </a>
        </nav>

        <div className="p-4 border-t border-slate-200 space-y-2">
          <button onClick={toggleTheme} className="sidebar-link w-full">
            {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            {isDark ? "Light Mode" : "Dark Mode"}
          </button>
          <button onClick={handleLogout} className="sidebar-link text-red-600 hover:text-red-700 w-full">
            <LogOut className="w-4 h-4" />
            Logout
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 lg:ml-64">
        <header className="sticky top-0 z-20 glass border-b border-slate-200/60">
          <div className="h-16 px-6 flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold text-slate-900">Pharmacist Dashboard</h1>
              <p className="text-xs text-slate-500">Manage prescriptions and medication queue</p>
            </div>
            <div className="flex items-center gap-3">
              {pendingCount > 0 && (
                <span className="badge badge-warning flex items-center gap-1">
                  <Bell className="w-3 h-3" />
                  {pendingCount} pending
                </span>
              )}
              <div className="w-9 h-9 rounded-full bg-violet-100 flex items-center justify-center">
                {pharmacist.profilePic ? (
                  <img src={pharmacist.profilePic} alt="Profile" className="w-9 h-9 rounded-full object-cover" />
                ) : (
                  <User className="w-5 h-5 text-violet-600" />
                )}
              </div>
            </div>
          </div>
        </header>

        <main className="p-6 space-y-6">
          <div className="grid md:grid-cols-3 gap-5">
            <div className="card rounded-2xl p-5">
              <div className="text-sm text-slate-500 mb-1">Total Prescriptions</div>
              <div className="text-3xl font-bold text-slate-900">{prescriptions.length}</div>
            </div>
            <div className="card rounded-2xl p-5">
              <div className="text-sm text-slate-500 mb-1">Pending</div>
              <div className="text-3xl font-bold text-amber-600">{pendingCount}</div>
            </div>
            <div className="card rounded-2xl p-5">
              <div className="text-sm text-slate-500 mb-1">Completed</div>
              <div className="text-3xl font-bold text-emerald-600">{prescriptions.filter(p => p.status === "Done").length}</div>
            </div>
          </div>

          <div className="grid lg:grid-cols-3 gap-6">
            <div className="lg:col-span-1 card rounded-2xl p-6">
              <h2 className="font-semibold text-slate-900 mb-4">Your Profile</h2>
              <div className="flex flex-col items-center">
                {pharmacist.profilePic ? (
                  <img src={pharmacist.profilePic} alt="Profile" className="w-20 h-20 rounded-full mb-3 object-cover shadow-sm" />
                ) : (
                  <div className="w-20 h-20 rounded-full bg-slate-100 flex items-center justify-center mb-3">
                    <User className="w-8 h-8 text-slate-400" />
                  </div>
                )}
                <span className="font-semibold text-sm text-slate-900">{pharmacist.name}</span>
                <label className="mt-3 cursor-pointer px-3 py-1.5 bg-violet-50 text-violet-700 rounded-lg text-xs font-medium hover:bg-violet-100 transition-colors">
                  Upload Picture
                  <input type="file" className="hidden" onChange={handleProfilePic} />
                </label>
              </div>
            </div>

            <div className="lg:col-span-2 card rounded-2xl p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl bg-orange-100 flex items-center justify-center">
                  <Pill className="text-orange-600 w-5 h-5" />
                </div>
                <div>
                  <h2 className="font-semibold text-slate-900">Prescription Queue</h2>
                  <p className="text-xs text-slate-500">View and fulfill prescriptions sent by doctors</p>
                </div>
              </div>

              <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
                <table className="w-full table-auto text-left">
                  <thead>
                    <tr className="bg-slate-50/80 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                      <th className="py-3 px-4">Patient</th>
                      <th className="py-3 px-4">Medication</th>
                      <th className="py-3 px-4">Doctor</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {prescriptions.filter(p => p.status === "Pending").map((p) => (
                      <tr key={p.id} className="hover:bg-slate-50/50 transition-colors">
                        <td className="py-3 px-4 text-sm text-slate-700">{p.patient}</td>
                        <td className="py-3 px-4 text-sm font-medium text-slate-900">{p.medication}</td>
                        <td className="py-3 px-4 text-sm text-slate-600">{p.doctor}</td>
                        <td className="py-3 px-4">
                          <span className="badge badge-warning">Pending</span>
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => markDone(p.id)}
                            className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-700 transition-colors"
                          >
                            Done
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Medication History */}
          <div className="card rounded-2xl p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-xl bg-yellow-100 flex items-center justify-center">
                <ClipboardList className="text-yellow-600 w-5 h-5" />
              </div>
              <div>
                <h2 className="font-semibold text-slate-900">Medication History</h2>
                <p className="text-xs text-slate-500">Past prescriptions handled by any pharmacist</p>
              </div>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
              <table className="w-full table-auto text-left">
                <thead>
                  <tr className="bg-slate-50/80 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    <th className="py-3 px-4">Patient</th>
                    <th className="py-3 px-4">Medication</th>
                    <th className="py-3 px-4">Doctor</th>
                    <th className="py-3 px-4">Pharmacist</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {prescriptions.map((p) => (
                    <tr key={p.id} className="hover:bg-slate-50/50 transition-colors">
                      <td className="py-3 px-4 text-sm text-slate-700">{p.patient}</td>
                      <td className="py-3 px-4 text-sm font-medium text-slate-900">{p.medication}</td>
                      <td className="py-3 px-4 text-sm text-slate-600">{p.doctor}</td>
                      <td className="py-3 px-4 text-sm text-slate-600">{p.pharmacist || "Unassigned"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
