import React, { useState, useEffect } from "react";
import { motion as Motion, AnimatePresence } from "framer-motion";
import { useNavigate, useLocation } from "react-router-dom";
import { CheckCircle, RefreshCw, Shield } from "lucide-react";

export default function OTPVerification() {
  const [otp, setOtp] = useState("");
  const [isVerifying, setIsVerifying] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);
  const [timer, setTimer] = useState(30);
  const [canResend, setCanResend] = useState(false);

  const navigate = useNavigate();
  const location = useLocation();
  const redirectPath = location.state?.redirectPath || "/doctor-dashboard";

  useEffect(() => {
    if (timer > 0) {
      const countdown = setTimeout(() => setTimer(timer - 1), 1000);
      return () => clearTimeout(countdown);
    } else {
      setCanResend(true);
    }
  }, [timer]);

  const handleVerify = (e) => {
    e.preventDefault();
    setIsVerifying(true);

    setTimeout(() => {
      if (otp === "1234") {
        setIsSuccess(true);
        setTimeout(() => {
          navigate(redirectPath);
        }, 2500);
      } else {
        alert("Invalid OTP. Try again.");
        setIsVerifying(false);
      }
    }, 1200);
  };

  const handleResend = () => {
    setCanResend(false);
    setTimer(30);
    alert("New OTP sent to your email!");
  };

  if (isSuccess) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center relative overflow-hidden">
        <div className="absolute inset-0">
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-green-500/10 rounded-full blur-3xl" />
        </div>
        <Motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", stiffness: 120, damping: 8 }}
          className="relative flex flex-col items-center text-center px-6"
        >
          <div className="w-20 h-20 rounded-full bg-green-500/20 flex items-center justify-center mb-6">
            <CheckCircle className="text-green-400 w-10 h-10" />
          </div>
          <h2 className="text-3xl font-bold text-white mb-2">Verification Successful!</h2>
          <p className="text-slate-400">Redirecting to your dashboard...</p>
        </Motion.div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center px-4 relative overflow-hidden">
      <div className="absolute inset-0">
        <div className="absolute top-0 left-1/4 w-96 h-96 bg-med-600/20 rounded-full blur-3xl animate-blob" />
        <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-blue-600/20 rounded-full blur-3xl animate-blob animation-delay-2000" />
      </div>

      <Motion.div
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        className="relative w-full max-w-md"
      >
        <div className="glass-dark rounded-3xl p-8 shadow-2xl">
          <div className="text-center mb-8">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-med-500 to-med-700 flex items-center justify-center mx-auto mb-4">
              <Shield className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-white mb-1">Verify Your Identity</h1>
            <p className="text-sm text-slate-400">Enter the 4-digit code sent to your email</p>
          </div>

          <form onSubmit={handleVerify} className="space-y-6">
            <div>
              <input
                type="text"
                placeholder="0000"
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 4))}
                className="input-field text-center text-3xl tracking-[0.5em] bg-slate-900/50 border-slate-700 text-white placeholder:text-slate-600"
                maxLength={4}
              />
            </div>

            <button
              type="submit"
              disabled={isVerifying || otp.length !== 4}
              className="w-full py-3.5 rounded-xl bg-gradient-to-r from-med-500 to-med-600 text-white font-semibold shadow-lg shadow-med-500/25 hover:shadow-med-500/40 transition-all hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:translate-y-0"
            >
              {isVerifying ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-5 w-5" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                  Verifying...
                </span>
              ) : "Verify OTP"}
            </button>
          </form>

          <div className="mt-6 text-center">
            {!canResend ? (
              <p className="text-sm text-slate-500">
                Resend available in <span className="font-semibold text-med-400">{timer}s</span>
              </p>
            ) : (
              <button
                onClick={handleResend}
                className="flex items-center justify-center gap-2 mx-auto text-sm font-medium text-med-400 hover:text-med-300 transition-colors"
              >
                <RefreshCw className="w-4 h-4" />
                Resend OTP
              </button>
            )}
          </div>
        </div>
      </Motion.div>
    </div>
  );
}
