import React, { useEffect, useRef, useState } from "react";
import {
  FaCog,
  FaVideo,
  FaUserMd,
  FaCalendarAlt,
  FaSearch,
  FaHistory,
  FaFlask,
  FaPills,
  FaHome,
  FaClipboardList,
  FaSignOutAlt,
} from "react-icons/fa";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Link } from "react-router-dom";

export default function PatientDashboard() {
  const storedName = localStorage.getItem("patientName") || "Patient";
  const [patientName, setPatientName] = useState(storedName);
  const [profilePic, setProfilePic] = useState(localStorage.getItem("profilePic") || "");

  const [showSettings, setShowSettings] = useState(false);
  const [nextOfKin, setNextOfKin] = useState(
    JSON.parse(localStorage.getItem("nextOfKin") || '{"name":"","id":""}')
  );
  const [password, setPassword] = useState("");

  const mapRef = useRef(null);

  const [selectedHospital, setSelectedHospital] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedDoctor, setSelectedDoctor] = useState(null);
  const [showBookModal, setShowBookModal] = useState(false);

  const [appointments, setAppointments] = useState(
    JSON.parse(localStorage.getItem("appointments") || "[]")
  );
  const [labHistory] = useState(
    JSON.parse(
      localStorage.getItem(
        "labHistory"
      ) || '["Blood Test (2025-09-10) - Normal","COVID-19 (2025-08-01) - Negative"]'
    )
  );
  const [pharmacyHistory] = useState(
    JSON.parse(
      localStorage.getItem("pharmacyHistory") ||
        '["Amoxicillin - 10 days (2025-09-11)","Vitamin D - 30 days (2025-08-15)]'
    )
  );

  // helper: get last hospital & doctor from appointments
  const lastVisit = appointments.length ? appointments[0] : null;

  const hospitals = {
    "Nairobi Hospital": [
      { name: "Dr. Kamau", specialty: "Cardiologist", available: ["2025-10-24", "2025-10-26"] },
      { name: "Dr. Atieno", specialty: "Dermatologist", available: ["2025-10-25", "2025-10-28"] },
    ],
    "Aga Khan": [
      { name: "Dr. Patel", specialty: "Neurologist", available: ["2025-10-23", "2025-10-24"] },
      { name: "Dr. Amina", specialty: "Pediatrician", available: ["2025-10-26", "2025-10-27"] },
    ],
    "Mater Hospital": [
      { name: "Dr. Ochieng", specialty: "Orthopedic", available: ["2025-10-25", "2025-10-28"] },
      { name: "Dr. Wanjiru", specialty: "Gynecologist", available: ["2025-10-24", "2025-10-26"] },
    ],
  };

  useEffect(() => {
    if (mapRef.current) return;

    const map = L.map("map", { zoomControl: true }).setView([-1.286389, 36.817223], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    const patientIcon = L.icon({
      iconUrl: "https://cdn-icons-png.flaticon.com/512/684/684908.png",
      iconSize: [34, 34],
      iconAnchor: [17, 34],
    });

    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const coords = [pos.coords.latitude, pos.coords.longitude];
          map.setView(coords, 13);
          L.marker(coords, { icon: patientIcon }).addTo(map).bindPopup("You are here");
        },
        () => {
          L.marker([-1.286389, 36.817223], { icon: patientIcon })
            .addTo(map)
            .bindPopup("Default location");
        },
        { enableHighAccuracy: true }
      );
    } else {
      L.marker([-1.286389, 36.817223], { icon: patientIcon }).addTo(map).bindPopup("You are here");
    }

    const hospitalList = [
      { name: "Nairobi Hospital", coords: [-1.2921, 36.8219] },
      { name: "Aga Khan University Hospital", coords: [-1.2684, 36.811] },
      { name: "MP Shah Hospital", coords: [-1.2654, 36.8129] },
      { name: "Kenyatta National Hospital", coords: [-1.3001, 36.8066] },
    ];
    hospitalList.forEach((h) => {
      L.marker(h.coords).addTo(map).bindPopup(`<b>${h.name}</b>`);
    });

    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  const filteredDoctors =
    selectedHospital && hospitals[selectedHospital]
      ? hospitals[selectedHospital].filter((doc) =>
          (doc.name + doc.specialty).toLowerCase().includes(searchTerm.toLowerCase())
        )
      : [];

  const saveSettings = () => {
    localStorage.setItem("nextOfKin", JSON.stringify(nextOfKin));
    if (profilePic) localStorage.setItem("profilePic", profilePic);
    if (patientName) localStorage.setItem("patientName", patientName);
    if (password) localStorage.setItem("userPassword", password);
    alert("Settings saved locally.");
    setShowSettings(false);
  };

  const bookAppointment = (doctor, date, hospitalName) => {
    const appt = {
      id: Date.now(),
      doctor: doctor.name,
      specialty: doctor.specialty,
      hospital: hospitalName,
      date,
      createdAt: new Date().toISOString(),
    };
    const next = [appt, ...appointments];
    setAppointments(next);
    localStorage.setItem("appointments", JSON.stringify(next));
    alert(`Appointment booked with ${doctor.name} on ${date}`);
    setShowBookModal(false);
    setSelectedDoctor(null);
  };

  const openBook = (doc, hospitalName) => {
    setSelectedDoctor({ ...doc, hospital: hospitalName });
    setShowBookModal(true);
  };

  const handleProfilePic = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setProfilePic(reader.result);
      localStorage.setItem("profilePic", reader.result);
    };
    reader.readAsDataURL(file);
  };

  const sidebarLinks = [
    { icon: FaHome, label: "Overview", href: "#" },
    { icon: FaClipboardList, label: "Appointments", href: "#" },
    { icon: FaHistory, label: "Medical History", href: "#" },
    { icon: FaFlask, label: "Lab Results", href: "#" },
    { icon: FaPills, label: "Pharmacy", href: "#" },
  ];

  return (
    <div className="min-h-screen bg-slate-50 flex text-slate-900">
      {/* Sidebar */}
      <aside className="hidden lg:flex flex-col w-64 bg-white border-r border-slate-200 fixed inset-y-0 left-0 z-30">
        <div className="h-16 flex items-center gap-3 px-6 border-b border-slate-200">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-med-500 to-med-700 flex items-center justify-center">
            <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" /></svg>
          </div>
          <span className="text-lg font-bold text-slate-900 tracking-tight">MedBeta</span>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          {sidebarLinks.map((link) => (
            <a key={link.label} href={link.href} className="sidebar-link">
              <link.icon className="w-4 h-4" />
              {link.label}
            </a>
          ))}
        </nav>

        <div className="p-4 border-t border-slate-200">
          <Link to="/portal-select" className="sidebar-link text-red-600 hover:text-red-700">
            <FaSignOutAlt className="w-4 h-4" />
            Logout
          </Link>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex-1 lg:ml-64">
        {/* Header */}
        <header className="sticky top-0 z-20 glass border-b border-slate-200/60">
          <div className="h-16 px-6 flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold text-slate-900">Patient Dashboard</h1>
              <p className="text-xs text-slate-500">Welcome back, {patientName}</p>
            </div>

            <div className="flex items-center gap-3">
              {lastVisit && (
                <div className="hidden sm:block text-right mr-2">
                  <div className="text-xs text-slate-500">Last visit</div>
                  <div className="text-xs font-medium text-slate-700">{lastVisit.doctor} @ {lastVisit.hospital}</div>
                </div>
              )}

              <label className="relative cursor-pointer">
                {profilePic ? (
                  <img src={profilePic} alt="Profile" className="w-10 h-10 rounded-full object-cover ring-2 ring-white shadow-sm" />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-gradient-to-br from-med-500 to-med-700 flex items-center justify-center text-white font-semibold text-sm shadow-sm">
                    {patientName?.slice(0, 1) || "P"}
                  </div>
                )}
                <input type="file" accept="image/*" className="absolute inset-0 opacity-0 cursor-pointer" onChange={handleProfilePic} title="Upload profile picture" />
              </label>

              <button onClick={() => setShowSettings(true)} className="p-2 rounded-lg hover:bg-slate-100 transition-colors" title="Settings">
                <FaCog className="text-slate-600 text-lg" />
              </button>
            </div>
          </div>
        </header>

        {/* Main */}
        <main className="p-6 space-y-6">
          {/* Top row: Medical / Lab / Pharmacy History */}
          <div className="grid md:grid-cols-3 gap-5">
            <section className="card rounded-2xl p-5">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-med-100 flex items-center justify-center">
                  <FaHistory className="text-med-600 text-sm" />
                </div>
                <h2 className="font-semibold text-slate-900">Medical History</h2>
              </div>
              {appointments.length === 0 ? (
                <p className="text-sm text-slate-500">No past appointments yet.</p>
              ) : (
                <ul className="space-y-2 max-h-40 overflow-y-auto pr-1">
                  {appointments.map((a) => (
                    <li key={a.id} className="p-3 bg-white rounded-xl border border-slate-100">
                      <div className="font-semibold text-sm text-med-900">{a.doctor}</div>
                      <div className="text-xs text-slate-500 mt-0.5">{a.specialty} — {a.hospital}</div>
                      <div className="text-xs text-slate-400 mt-0.5">Date: {a.date}</div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card rounded-2xl p-5">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-emerald-100 flex items-center justify-center">
                  <FaFlask className="text-emerald-600 text-sm" />
                </div>
                <h2 className="font-semibold text-slate-900">Lab Results</h2>
              </div>
              <ul className="space-y-2 max-h-40 overflow-y-auto pr-1">
                {labHistory.map((l, i) => (
                  <li key={i} className="p-3 bg-white rounded-xl border border-slate-100 text-sm text-slate-700">{l}</li>
                ))}
              </ul>
            </section>

            <section className="card rounded-2xl p-5">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-amber-100 flex items-center justify-center">
                  <FaPills className="text-amber-600 text-sm" />
                </div>
                <h2 className="font-semibold text-slate-900">Pharmacy History</h2>
              </div>
              <ul className="space-y-2 max-h-40 overflow-y-auto pr-1">
                {pharmacyHistory.map((p, i) => (
                  <li key={i} className="p-3 bg-white rounded-xl border border-slate-100 text-sm text-slate-700">{p}</li>
                ))}
              </ul>
            </section>
          </div>

          {/* Doctor search & selector + booking controls */}
          <div className="grid lg:grid-cols-2 gap-5">
            <div className="card rounded-2xl p-6">
              <h2 className="text-lg font-bold text-slate-900 mb-4">Find a Doctor</h2>

              <select
                className="input-field mb-3"
                value={selectedHospital}
                onChange={(e) => {
                  setSelectedHospital(e.target.value);
                  setSelectedDoctor(null);
                }}
              >
                <option value="">Select Hospital</option>
                {Object.keys(hospitals).map((h) => (
                  <option key={h} value={h}>{h}</option>
                ))}
              </select>

              <div className="flex items-center border border-slate-200 rounded-xl mb-3 p-2.5 bg-white">
                <FaSearch className="text-slate-400 mr-2.5 text-sm" />
                <input
                  type="text"
                  placeholder="Search doctor or specialty..."
                  className="flex-1 outline-none bg-transparent text-sm text-slate-700 placeholder:text-slate-400"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  disabled={!selectedHospital}
                />
              </div>

              {selectedHospital && (
                <>
                  {filteredDoctors.length > 0 ? (
                    <div className="space-y-2.5">
                      {filteredDoctors.map((doc) => (
                        <div key={doc.name} className="p-4 bg-white rounded-xl border border-slate-100 hover:border-med-200 hover:shadow-sm transition-all">
                          <div className="flex justify-between items-start">
                            <div>
                              <p className="font-semibold text-sm text-slate-900 flex items-center gap-2">
                                <FaUserMd className="text-med-600" />
                                {doc.name}
                              </p>
                              <p className="text-xs text-slate-500 mt-1">{doc.specialty}</p>
                            </div>

                            <div className="flex flex-col gap-2 w-36">
                              <button
                                onClick={() => openBook(doc, selectedHospital)}
                                className="w-full bg-med-600 text-white py-1.5 rounded-lg hover:bg-med-700 text-xs font-medium transition-colors"
                              >
                                <FaCalendarAlt className="inline mr-1.5" />
                                Book
                              </button>
                              <button
                                onClick={() => alert(`Starting remote call with ${doc.name}`)}
                                className="w-full bg-emerald-600 text-white py-1.5 rounded-lg hover:bg-emerald-700 text-xs font-medium transition-colors"
                              >
                                <FaVideo className="inline mr-1.5" />
                                Call
                              </button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-slate-500">No doctors found for this hospital.</p>
                  )}
                </>
              )}
            </div>

            {/* Map */}
            <div className="card rounded-2xl p-5">
              <h2 className="text-base font-semibold text-slate-900 mb-3">Nearby Hospitals</h2>
              <div id="map" className="w-full h-[280px] rounded-xl border border-slate-200" />
            </div>
          </div>
        </main>
      </div>

      {/* Book Modal */}
      {showBookModal && selectedDoctor && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="bg-white rounded-2xl p-6 w-full max-w-md shadow-2xl"
          >
            <h3 className="text-lg font-bold text-slate-900 mb-1">
              Available dates
            </h3>
            <p className="text-sm text-slate-500 mb-4">
              {selectedDoctor.name} — {selectedDoctor.specialty}
            </p>

            <div className="space-y-2">
              {(selectedDoctor.available || []).map((d) => (
                <button
                  key={d}
                  onClick={() => bookAppointment(selectedDoctor, d, selectedDoctor.hospital || selectedDoctor.hospitalName)}
                  className="w-full p-3 border border-slate-200 rounded-xl hover:bg-med-50 hover:border-med-200 text-sm font-medium text-slate-700 transition-colors"
                >
                  {d}
                </button>
              ))}
              {(!selectedDoctor.available || selectedDoctor.available.length === 0) && (
                <div className="text-sm text-slate-500">No available dates listed.</div>
              )}
            </div>

            <div className="mt-5 flex gap-2">
              <button
                onClick={() => {
                  setShowBookModal(false);
                  setSelectedDoctor(null);
                }}
                className="flex-1 btn-secondary"
              >
                Cancel
              </button>
            </div>
          </motion.div>
        </div>
      )}

      {/* Settings Modal */}
      {showSettings && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="bg-white rounded-2xl p-6 w-full max-w-md shadow-2xl"
          >
            <h3 className="text-lg font-bold text-slate-900 mb-5">Settings</h3>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Display Name</label>
                <input
                  type="text"
                  value={patientName}
                  onChange={(e) => setPatientName(e.target.value)}
                  className="input-field"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Upload Profile Picture</label>
                <input type="file" accept="image/*" onChange={handleProfilePic} className="w-full text-sm text-slate-600" />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Next of Kin Name</label>
                <input
                  type="text"
                  value={nextOfKin.name}
                  onChange={(e) => setNextOfKin({ ...nextOfKin, name: e.target.value })}
                  className="input-field"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Next of Kin ID / Birth Cert No.</label>
                <input
                  type="text"
                  value={nextOfKin.id}
                  onChange={(e) => setNextOfKin({ ...nextOfKin, id: e.target.value })}
                  className="input-field"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">Change Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="New password"
                  className="input-field"
                />
              </div>

              <div className="flex gap-2 pt-2">
                <button onClick={saveSettings} className="flex-1 btn-primary">Save</button>
                <button onClick={() => setShowSettings(false)} className="flex-1 btn-secondary">Cancel</button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}
