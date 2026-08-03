import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { User, KeyRound } from "lucide-react";

export default function LoginForm({ role }) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const navigate = useNavigate();

  const handleLogin = () => {
    if (!id || !name) return alert("Enter ID and name");

    if (!id.toLowerCase().startsWith(role[0])) {
      return alert(`ID must start with ${role[0].toUpperCase()}`);
    }

    localStorage.setItem("userRole", role);
    localStorage.setItem("userName", name);
    navigate(`/${role}-dashboard`);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center px-4 relative overflow-hidden">
      <div className="absolute inset-0">
        <div className="absolute top-0 left-1/4 w-96 h-96 bg-med-600/20 rounded-full blur-3xl animate-blob" />
      </div>
      <div className="relative w-full max-w-sm">
        <div className="glass-dark rounded-3xl p-8 shadow-2xl">
          <div className="text-center mb-8">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-med-500 to-med-700 flex items-center justify-center mx-auto mb-4">
              <User className="w-6 h-6 text-white" />
            </div>
            <h2 className="text-2xl font-bold text-white mb-1">{role?.toUpperCase()} Login</h2>
            <p className="text-sm text-slate-400">Enter your credentials to continue</p>
          </div>

          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 ml-1">ID</label>
              <div className="relative">
                <KeyRound className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="text"
                  placeholder="Enter your ID"
                  value={id}
                  onChange={(e) => setId(e.target.value)}
                  className="input-field pl-10 bg-slate-900/50 border-slate-700 text-white placeholder:text-slate-500"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 ml-1">Name</label>
              <div className="relative">
                <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="text"
                  placeholder="Enter your name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="input-field pl-10 bg-slate-900/50 border-slate-700 text-white placeholder:text-slate-500"
                />
              </div>
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
