import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowLeft, ShieldCheck } from "lucide-react";

const NotFound = () => {
  const location = useLocation();

  useEffect(() => {
    console.error("404 Error: User attempted to access non-existent route:", location.pathname);
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#FAFCFF] px-6 text-[#25263A]">
      <div className="w-full max-w-md rounded-2xl border border-[#E4E8F2] bg-white p-8 text-center shadow-[0_12px_35px_rgba(37,38,58,0.07)]">
        <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-[#91C8FF] text-[#25263A] shadow-[0_0_0_6px_rgba(232,244,255,1)]"><ShieldCheck className="h-5 w-5" /></div>
        <p className="mt-6 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-[#2f8ce0]">ULPF · Not found</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-[#25263A]">Route unavailable</h1>
        <p className="mt-3 text-sm leading-relaxed text-[#72748A]">The requested workspace view does not exist. Return to the operational dashboard to continue.</p>
        <Link to="/" className="mt-6 inline-flex h-10 items-center gap-2 rounded-lg bg-[#91C8FF] px-4 text-xs font-semibold text-[#25263A] transition-colors hover:bg-[#7dbbff]"><ArrowLeft className="h-3.5 w-3.5" />Return to dashboard</Link>
      </div>
    </div>
  );
};

export default NotFound;