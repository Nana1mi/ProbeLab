export const metadata = { title: "VELA · Research workspace" };

export default function VelaResearchPage() {
  return (
    <section style={{ height: "100%", minHeight: "calc(100dvh - 64px)", width: "100%" }}>
      <iframe
        src="/vela-proxy/"
        title="VELA verifier-guided research workspace"
        style={{ width: "100%", height: "calc(100dvh - 64px)", border: 0 }}
      />
    </section>
  );
}
