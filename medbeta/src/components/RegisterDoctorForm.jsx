import React, { useState } from "react";

export default function RegisterDoctorForm({ onCreated }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [department, setDepartment] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name || !email) return alert("Please fill required fields");

    setLoading(true);
    try {
      const res = await fetch("http://localhost:5000/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          email,
          role: "doctor",
          status: "pending",
          meta: { department },
        }),
      });
      const data = await res.json();
      if (data.success || res.ok) {
        alert("Doctor registered successfully!");
        setName("");
        setEmail("");
        setDepartment("");
        onCreated?.();
      } else {
        alert("Error: " + (data.error || JSON.stringify(data)));
      }
    } catch {
      alert("Could not connect to server.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-xs font-medium text-slate-700 mb-1.5">Full Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="input-field"
          placeholder="Dr. John Doe"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-700 mb-1.5">Email</label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="input-field"
          placeholder="doctor@hospital.com"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-700 mb-1.5">Department / Specialty</label>
        <input
          type="text"
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
          className="input-field"
          placeholder="Cardiology"
        />
      </div>

      <button type="submit" disabled={loading} className="w-full btn-primary py-2.5">
        {loading ? "Registering..." : "Register Doctor"}
      </button>
    </form>
  );
}
