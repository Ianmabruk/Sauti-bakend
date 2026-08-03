import React, { useState, useMemo } from "react";
import { Calendar, FileText, User, FlaskRound, Pill, Activity, Home, ClipboardList, Users, Settings, LogOut } from "lucide-react";
import { motion as Motion, AnimatePresence } from "framer-motion";

export default function DoctorDashboard() {
  const [profilePic, setProfilePic] = useState("https://via.placeholder.com/100");
  const [doctorName] = useState("Dr. Ian Mabruk");
  const [specialty] = useState("Cardiologist");

  const [activeTab, setActiveTab] = useState("bookings");
  const [status, setStatus] = useState("Available");

  const [notes, setNotes] = useState("");
  const [labOrders, setLabOrders] = useState("");
  const [prescriptions, setPrescriptions] = useState("");

  const [appointments, setAppointments] = useState([
    { id: 1, date: "2025-10-21", time: "10:00 AM", patient: "Jane Doe", status: "pending" },
    { id: 2, date: "2025-10-21", time: "11:30 AM", patient: "John Smith", status: "pending" },
    { id: 3, date: "2025-10-22", time: "02:00 PM", patient: "Alice Johnson", status: "pending" },
  ]);

  const [records, setRecords] = useState([]);
  const [remoteRecords, setRemoteRecords] = useState([]);
  const [accessKey, setAccessKey] = useState("");

  const [selectedBooking, setSelectedBooking] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");

  const [confirmationModal, setConfirmationModal] = useState({ isOpen: false, appointment: null });

  const handleImageChange = (e) => {
    const file = e.target.files?.[0];
    if (file) setProfilePic(URL.createObjectURL(file));
  };

  const handleAppointmentAction = (appointment, action) => {
    setAppointments(prev =>
      prev.map(a =>
        a.id === appointment.id ? { ...a, status: action } : a
      )
    );
    setConfirmationModal({ isOpen: false, appointment: null });

    if (action === "confirmed") {
      alert(`${appointment.patient}'s appointment confirmed!`);
    } else if (action === "rejected") {
      alert(`${appointment.patient}'s appointment was rejected!`);
    }
  };

  const handleSaveRecord = () => {
    if (!notes.trim() && !labOrders.trim() && !prescriptions.trim())
      return alert("Please enter consultation info.");

    const newRec = {
      id: Date.now(),
      patient: selectedBooking?.patient || "New Patient",
      doctor: `${doctorName} (Your Clinic)`,
      date: new Date().toISOString().slice(0, 10),
      notes: `Notes: ${notes}\nLab Orders: ${labOrders}\nPrescription: ${prescriptions}`,
    };
    setRecords(prev => [newRec, ...prev]);
    setNotes(""); setLabOrders(""); setPrescriptions("");
    alert("Record saved successfully!");
  };

  const sendLabRequest = () => {
    if (!labOrders.trim()) return alert("Add lab orders first.");
    alert(`Lab request sent:\n${labOrders}`);
  };

  const sendPharmaRequest = () => {
    if (!prescriptions.trim()) return alert("Add prescription first.");
    alert(`Pharmacist request sent:\n${prescriptions}`);
  };

  const fetchRemoteRecords = () => {
    if (!accessKey.trim()) return alert("Enter access key to fetch records.");
    const fakeRemoteRecords = [
      { id: 101, patient: "Remote Patient A", doctor: "Dr. Remote", date: "2025-08-15", notes: "Remote notes example" },
      { id: 102, patient: "Remote Patient B", doctor: "Dr. Remote", date: "2025-09-10", notes: "Another remote notes" },
    ];
    setRemoteRecords(fakeRemoteRecords);
    alert("Access granted. Remote records loaded.");
  };

  const filteredRecords = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return [...records, ...remoteRecords];
    return [...records, ...remoteRecords].filter(
      r => r.patient.toLowerCase().includes(q) || (r.notes || "").toLowerCase().includes(q)
    );
  }, [records, remoteRecords, searchQuery]);

  const getAppointmentsCount = (dateStr) => appointments.filter(a => a.date === dateStr && a.status === "confirmed").length;

  const sidebarLinks = [
    { icon: Home, label: "Dashboard", active: activeTab === "bookings" ? false : true },
    { icon: ClipboardList, label: "Bookings", active: activeTab === "bookings" },
    { icon: FileText, label: "Records", active: activeTab === "records" },
  ];

  return (
    <div className="min-h-screen bg-slate-50 flex text-slate-900">
      {/* Sidebar */}
      <aside className="hidden lg:flex flex-col w-64 bg-slate-900 text-white fixed inset-y-0 left-0 z-30">
        <div className="h-16 flex items-center gap-3 px-6 border-b border-slate-800">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-teal-500 to-emerald-600 flex items-center justify-center">
            <Activity className="w-5 h-5 text-white" />
          </div>
          <span className="text-lg font-bold tracking-tight">MedBeta</span>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          {sidebarLinks.map((link) => (
            <button
              key={link.label}
              onClick={() => setActiveTab(link.active ? "records" : "bookings")}
              className={`sidebar-link ${link.active ? "active" : ""}`}
            >
              <link.icon className="w-4 h-4" />
              {link.label}
            </button>
          ))}
        </nav>

        <div className="p-4 border-t border-slate-800">
          <div className="flex items-center gap-3 px-2 py-2">
            <img src={profilePic} alt="Doctor" className="w-8 h-8 rounded-full object-cover ring-2 ring-slate-700" />
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate">{doctorName}</div>
              <div className="text-xs text-slate-400 truncate">{specialty}</div>
            </div>
          </div>
          <button className="sidebar-link text-red-400 hover:text-red-300 mt-2 w-full">
            <LogOut className="w-4 h-4" />
            Logout
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex-1 lg:ml-64">
        {/* Header */}
        <header className="sticky top-0 z-20 glass border-b border-slate-200/60">
          <div className="h-16 px-6 flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold text-slate-900">Doctor Portal</h1>
              <p className="text-xs text-slate-500">Manage bookings, patient records, and consultations</p>
            </div>

            <div className="flex items-center gap-3">
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="text-xs font-medium px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-200"
              >
                <option>Available</option>
                <option>On Break</option>
                <option>In Lunch</option>
                <option>Offline</option>
              </select>

              <label className="relative cursor-pointer">
                <img src={profilePic} alt="Doctor" className="w-9 h-9 rounded-full object-cover ring-2 ring-white shadow-sm" />
                <input type="file" accept="image/*" className="absolute inset-0 opacity-0 cursor-pointer" onChange={handleImageChange} />
              </label>
            </div>
          </div>
        </header>

        {/* Main */}
        <main className="p-6">
          {/* Top controls */}
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mb-6">
            <div>
              <div className="page-header">
                <h1>Welcome back, <span className="text-slate-900">{doctorName}</span></h1>
                <p>Manage bookings, patient records, and consultation panels below.</p>
              </div>
            </div>

            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
              <div className="flex gap-2">
                <button
                  onClick={() => setActiveTab("bookings")}
                  className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${
                    activeTab === "bookings"
                      ? "bg-teal-600 text-white shadow-lg shadow-teal-600/20"
                      : "bg-white text-slate-700 border border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  Manage Bookings
                </button>
                <button
                  onClick={() => setActiveTab("records")}
                  className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${
                    activeTab === "records"
                      ? "bg-teal-600 text-white shadow-lg shadow-teal-600/20"
                      : "bg-white text-slate-700 border border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  Patient Records
                </button>
              </div>

              <div className="w-full sm:w-64">
                <div className="relative">
                  <FaSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs" />
                  <input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="input-field pl-9 text-sm"
                    placeholder="Search patient..."
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Grid */}
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            {/* Left - Bookings & Records */}
            <div className="xl:col-span-2 space-y-6">
              {activeTab === "bookings" && (
                <section className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                  <div className="p-6 border-b border-slate-100">
                    <h3 className="text-lg font-bold text-slate-900">Manage Bookings</h3>
                    <p className="text-sm text-slate-500 mt-0.5">Review and manage patient appointment requests</p>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full table-auto text-left">
                      <thead>
                        <tr className="bg-slate-50/80 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                          <th className="py-3 px-6">Patient</th>
                          <th className="py-3 px-6">Date</th>
                          <th className="py-3 px-6">Time</th>
                          <th className="py-3 px-6">Status</th>
                          <th className="py-3 px-6 text-right">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {appointments.map(a => (
                          <tr key={a.id} className="hover:bg-slate-50/50 transition-colors">
                            <td className="py-4 px-6">
                              <div className="flex items-center gap-3">
                                <div className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-xs font-semibold text-slate-600">
                                  {a.patient.split(" ").map(n => n[0]).join("")}
                                </div>
                                <span className="font-medium text-sm text-slate-900">{a.patient}</span>
                              </div>
                            </td>
                            <td className="py-4 px-6 text-sm text-slate-600">{a.date}</td>
                            <td className="py-4 px-6 text-sm text-slate-600">{a.time}</td>
                            <td className="py-4 px-6">
                              <span className={`badge ${a.status === "confirmed" ? "badge-success" : a.status === "rejected" ? "badge-danger" : "badge-warning"}`}>
                                {a.status}
                              </span>
                            </td>
                            <td className="py-4 px-6">
                              <div className="flex items-center justify-end gap-2">
                                <button
                                  onClick={() => setConfirmationModal({ isOpen: true, appointment: a })}
                                  disabled={a.status !== "pending"}
                                  className="px-3 py-1.5 rounded-lg bg-teal-600 text-white text-xs font-medium hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                >
                                  Confirm
                                </button>
                                <button
                                  onClick={() => handleAppointmentAction(a, "rejected")}
                                  disabled={a.status !== "pending"}
                                  className="px-3 py-1.5 rounded-lg border border-slate-200 text-slate-700 text-xs font-medium hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                >
                                  Reject
                                </button>
                                <button
                                  onClick={() => setSelectedBooking(a)}
                                  className="px-3 py-1.5 rounded-lg border border-slate-200 text-slate-700 text-xs font-medium hover:bg-slate-50 transition-colors"
                                >
                                  Consult
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Consultation Panel */}
                  <AnimatePresence>
                    {selectedBooking && (
                      <Motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.3 }}
                        className="border-t border-slate-100 bg-slate-50/50"
                      >
                        <div className="p-6">
                          <h4 className="text-base font-semibold text-slate-900 mb-4">
                            {selectedBooking.patient} — Consultation
                          </h4>
                          <div className="grid md:grid-cols-3 gap-4">
                            <div className="md:col-span-3">
                              <label className="block text-xs font-medium text-slate-600 mb-1.5">Doctor's Notes</label>
                              <textarea
                                value={notes}
                                onChange={e => setNotes(e.target.value)}
                                className="input-field h-24 resize-none text-sm"
                                placeholder="Write clinical notes..."
                              />
                            </div>
                            <div>
                              <label className="block text-xs font-medium text-slate-600 mb-1.5">Lab Orders</label>
                              <textarea
                                value={labOrders}
                                onChange={e => setLabOrders(e.target.value)}
                                className="input-field h-20 resize-none text-sm"
                                placeholder="Request lab tests..."
                              />
                              <button onClick={sendLabRequest} className="mt-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-700 transition-colors">Send Lab Request</button>
                            </div>
                            <div>
                              <label className="block text-xs font-medium text-slate-600 mb-1.5">Prescription</label>
                              <textarea
                                value={prescriptions}
                                onChange={e => setPrescriptions(e.target.value)}
                                className="input-field h-20 resize-none text-sm"
                                placeholder="Prescribe medicine..."
                              />
                              <button onClick={sendPharmaRequest} className="mt-2 px-4 py-2 rounded-lg bg-purple-600 text-white text-xs font-medium hover:bg-purple-700 transition-colors">Send to Pharmacist</button>
                            </div>
                            <div className="md:col-span-3 flex justify-end gap-2 pt-2">
                              <button onClick={() => setSelectedBooking(null)} className="btn-secondary">Cancel</button>
                              <button onClick={handleSaveRecord} className="btn-primary">Save Record</button>
                            </div>
                          </div>
                        </div>
                      </Motion.div>
                    )}
                  </AnimatePresence>
                </section>
              )}

              {/* Patient Records */}
              {activeTab === "records" && (
                <section className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                  <div className="p-6 border-b border-slate-100">
                    <h3 className="text-lg font-bold text-slate-900">Patient Records</h3>
                    <p className="text-sm text-slate-500 mt-0.5">Access local and remote patient records</p>
                  </div>
                  <div className="p-6">
                    <div className="flex gap-2 mb-4">
                      <input
                        value={accessKey}
                        onChange={e => setAccessKey(e.target.value)}
                        placeholder="Enter access key for remote records"
                        className="input-field text-sm"
                      />
                      <button onClick={fetchRemoteRecords} className="btn-primary text-sm">Access</button>
                    </div>
                    <div className="space-y-3">
                      {filteredRecords.length === 0 && <div className="text-sm text-slate-500 py-8 text-center">No records found.</div>}
                      {filteredRecords.map(r => (
                        <div key={r.id} className="p-4 rounded-xl border border-slate-200 bg-white hover:shadow-sm transition-shadow">
                          <div className="flex items-center justify-between mb-1">
                            <p className="font-medium text-sm text-slate-900">{r.patient}</p>
                            <span className="text-xs text-slate-400">{r.date}</span>
                          </div>
                          <p className="text-xs text-slate-500 mb-2">{r.doctor}</p>
                          <p className="text-sm text-slate-600 whitespace-pre-wrap">{r.notes}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                </section>
              )}
            </div>

            {/* Right Column */}
            <div className="space-y-6">
              <aside className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                <div className="flex flex-col items-center">
                  <img src={profilePic} alt="Doctor" className="w-20 h-20 rounded-full object-cover border-4 border-white shadow-lg" />
                  <label className="mt-3 cursor-pointer text-xs font-medium text-teal-600 hover:text-teal-700 underline underline-offset-2">
                    Change Photo
                    <input type="file" accept="image/*" className="hidden" onChange={handleImageChange} />
                  </label>
                  <div className="mt-4 text-center">
                    <h4 className="font-semibold text-slate-900">{doctorName}</h4>
                    <p className="text-xs text-slate-500">{specialty}</p>
                  </div>
                  <div className="mt-4 w-full">
                    <label className="block text-xs font-medium text-slate-600 mb-1.5">Status</label>
                    <select value={status} onChange={e => setStatus(e.target.value)} className="input-field text-sm">
                      <option>Available</option>
                      <option>On Break</option>
                      <option>In Lunch</option>
                      <option>Offline</option>
                    </select>
                  </div>
                </div>
              </aside>

              {/* Futuristic Calendar */}
              <aside className="bg-slate-900 text-white p-6 rounded-2xl shadow-lg">
                <h4 className="font-semibold text-sm mb-4 flex items-center gap-2">
                  <Calendar className="w-4 h-4 text-teal-400" />
                  Appointments Calendar
                </h4>
                <div className="grid grid-cols-7 gap-1.5">
                  {Array.from({ length: 30 }).map((_, i) => {
                    const dateStr = `2025-10-${(i + 1).toString().padStart(2, "0")}`;
                    const count = getAppointmentsCount(dateStr);
                    return (
                      <div
                        key={i}
                        className={`p-2 text-center rounded-lg cursor-pointer transition-all hover:scale-105 text-xs ${
                          count ? "bg-teal-600/80 ring-1 ring-teal-400 font-semibold" : "bg-white/10 text-slate-400"
                        }`}
                      >
                        {i + 1}
                        {count ? <span className="block text-[10px] mt-0.5 text-teal-100">{count} appt</span> : null}
                      </div>
                    );
                  })}
                </div>
              </aside>
            </div>
          </div>
        </main>
      </div>

      {/* Confirm Modal */}
      {confirmationModal.isOpen && (
        <div className="fixed inset-0 flex items-center justify-center z-50 p-4">
          <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={() => setConfirmationModal({ isOpen: false, appointment: null })} />
          <Motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="relative bg-white rounded-2xl w-full max-w-sm p-6 shadow-2xl"
          >
            <h3 className="text-lg font-bold text-slate-900 mb-1">Confirm Appointment</h3>
            <p className="text-sm text-slate-500 mb-5">Do you want to confirm {confirmationModal.appointment?.patient}'s appointment?</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmationModal({ isOpen: false, appointment: null })} className="btn-secondary">Cancel</button>
              <button onClick={() => handleAppointmentAction(confirmationModal.appointment, "confirmed")} className="btn-primary">Confirm</button>
            </div>
          </Motion.div>
        </div>
      )}
    </div>
  );
}
