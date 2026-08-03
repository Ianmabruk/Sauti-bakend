import React, { useState, useEffect } from "react";
import { FlaskRound, LogOut, User, UploadCloud } from "lucide-react";

export default function LabDashboard() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [technician, setTechnician] = useState({
    name: "",
    profilePic: null,
    notes: "",
  });
  const [nameInput, setNameInput] = useState("");

  const [doctors] = useState([
    { id: 1, name: "Dr. John Doe", specialty: "Pathology" },
    { id: 2, name: "Dr. Jane Smith", specialty: "Microbiology" },
  ]);

  const [patients] = useState([
    { id: 1, name: "Patient A" },
    { id: 2, name: "Patient B" },
  ]);

  const [testRequests, setTestRequests] = useState([
    { id: 1, patientId: 1, test: "Blood Test", doctorId: 1, status: "Pending" },
    { id: 2, patientId: 2, test: "Urine Test", doctorId: 2, status: "Pending" },
  ]);

  useEffect(() => {
    if (loggedIn) {
      localStorage.setItem("loggedIn", "true");
      localStorage.setItem("technician", JSON.stringify(technician));
    }
  }, [loggedIn, technician]);

  const handleLogin = () => {
    if (!nameInput.trim()) return alert("Please enter your name.");
    setTechnician({ ...technician, name: nameInput.trim() });
    setLoggedIn(true);
  };

  const handleLogout = () => {
    setLoggedIn(false);
    setTechnician({ name: "", profilePic: null, notes: "" });
    setNameInput("");
    localStorage.removeItem("loggedIn");
    localStorage.removeItem("technician");
  };

  const handleProfilePic = (e) => {
    setTechnician({ ...technician, profilePic: URL.createObjectURL(e.target.files[0]) });
  };

  const handleNotesChange = (e) => {
    setTechnician({ ...technician, notes: e.target.value });
  };

  const completeTest = (id) => {
    setTestRequests((prev) =>
      prev.map((t) => (t.id === id ? { ...t, status: "Completed" } : t))
    );
  };

  const getPatientName = (id) => patients.find((p) => p.id === id)?.name;
  const getDoctorName = (id) => doctors.find((d) => d.id === id)?.name;

  if (!loggedIn) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center px-4 relative overflow-hidden">
        <div className="absolute inset-0">
          <div className="absolute top-0 left-1/4 w-96 h-96 bg-emerald-600/20 rounded-full blur-3xl animate-blob" />
        </div>
        <div className="relative w-full max-w-sm">
          <div className="glass-dark rounded-3xl p-8 shadow-2xl">
            <div className="text-center mb-8">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center mx-auto mb-4">
                <FlaskRound className="w-6 h-6 text-white" />
              </div>
              <h2 className="text-2xl font-bold text-white mb-1">Lab Technician</h2>
              <p className="text-sm text-slate-400">Sign in to access the lab portal</p>
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
              <button onClick={handleLogin} className="w-full btn-primary py-3">
                Sign In
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 flex text-slate-900">
      {/* Sidebar */}
      <aside className="hidden lg:flex flex-col w-64 bg-white border-r border-slate-200 fixed inset-y-0 left-0 z-30">
        <div className="h-16 flex items-center gap-3 px-6 border-b border-slate-200">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center">
            <FlaskRound className="w-5 h-5 text-white" />
          </div>
          <span className="text-lg font-bold text-slate-900 tracking-tight">Lab Portal</span>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          <a href="#" className="sidebar-link active">
            <FlaskRound className="w-4 h-4" />
            Dashboard
          </a>
        </nav>

        <div className="p-4 border-t border-slate-200">
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
              <h1 className="text-lg font-bold text-slate-900">Laboratory Dashboard</h1>
              <p className="text-xs text-slate-500">Process test requests and manage results</p>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-full bg-emerald-100 flex items-center justify-center">
                <User className="w-5 h-5 text-emerald-600" />
              </div>
              <span className="text-sm font-medium text-slate-700">{technician.name}</span>
            </div>
          </div>
        </header>

        <main className="p-6">
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            {/* Technician Profile */}
            <div className="card rounded-2xl p-6">
              <h2 className="font-semibold text-slate-900 mb-4">Your Profile</h2>
              <div className="flex flex-col items-center">
                {technician.profilePic ? (
                  <img src={technician.profilePic} alt="Profile" className="w-20 h-20 rounded-full mb-3 object-cover shadow-sm" />
                ) : (
                  <div className="w-20 h-20 rounded-full bg-slate-100 flex items-center justify-center mb-3">
                    <User className="w-8 h-8 text-slate-400" />
                  </div>
                )}
                <span className="font-semibold text-sm text-slate-900">{technician.name}</span>
                <label className="mt-3 cursor-pointer px-3 py-1.5 bg-emerald-50 text-emerald-700 rounded-lg text-xs font-medium hover:bg-emerald-100 transition-colors flex items-center gap-1.5">
                  <UploadCloud className="w-3.5 h-3.5" />
                  Upload Picture
                  <input type="file" className="hidden" onChange={handleProfilePic} />
                </label>

                <textarea
                  placeholder="Add your private notes..."
                  value={technician.notes}
                  onChange={handleNotesChange}
                  className="mt-4 input-field h-24 resize-none text-sm"
                />
              </div>
            </div>

            {/* Test Requests */}
            <div className="card rounded-2xl p-6 lg:col-span-3">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl bg-emerald-100 flex items-center justify-center">
                  <FlaskRound className="text-emerald-600 w-5 h-5" />
                </div>
                <div>
                  <h2 className="font-semibold text-slate-900">Lab Test Requests</h2>
                  <p className="text-xs text-slate-500">All tests requested by doctors</p>
                </div>
              </div>

              <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
                <table className="w-full table-auto text-left">
                  <thead>
                    <tr className="bg-slate-50/80 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                      <th className="py-3 px-4">Patient</th>
                      <th className="py-3 px-4">Test</th>
                      <th className="py-3 px-4">Doctor</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {testRequests.map((tr) => (
                      <tr key={tr.id} className="hover:bg-slate-50/50 transition-colors">
                        <td className="py-3 px-4 text-sm text-slate-700">{getPatientName(tr.patientId)}</td>
                        <td className="py-3 px-4 text-sm font-medium text-slate-900">{tr.test}</td>
                        <td className="py-3 px-4 text-sm text-slate-600">{getDoctorName(tr.doctorId)}</td>
                        <td className="py-3 px-4">
                          <span className={`badge ${tr.status === "Completed" ? "badge-success" : "badge-warning"}`}>
                            {tr.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-right">
                          {tr.status === "Pending" && (
                            <button
                              onClick={() => completeTest(tr.id)}
                              className="px-3 py-1.5 bg-emerald-600 text-white text-xs font-medium rounded-lg hover:bg-emerald-700 transition-colors"
                            >
                              Complete
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
