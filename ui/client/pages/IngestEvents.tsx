import { useRef } from "react";
import { PageHeader } from "@/components/common/bits";
import NormalizerForm from "@/components/normalizer/NormalizerForm";
import ResultsView from "@/components/normalizer/ResultsView";
import { useNormalizer } from "@/lib/normalize-context";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function IngestEvents() {
  const { result } = useNormalizer();
  const resultsRef = useRef<HTMLDivElement>(null);

  return (
    <>
      <PageHeader
        eyebrow="Events · Ingest"
        title="Ingest events"
        subtitle="Paste or upload logs from any source. Every line is parsed for real fields only — nothing is fabricated — and mapped to an OCSF event class with valid enum values."
      />
      <NormalizerForm onDone={() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })} />
      <div ref={resultsRef} className="scroll-mt-20">
        <ResultsView />
      </div>
      {!result && (
        <div className="mt-10 grid gap-3 sm:grid-cols-3">
          {[
            { title: "Real values only", body: "No placeholders, no fabricated fields. Only what was actually in your log line is emitted into the OCSF event." },
            { title: "Verified enum ids", body: "class_uid, activity_id, severity_id, status_id, action_id and disposition_id come from the published OCSF 1.3.0 attribute tables." },
            { title: "Noise rejected", body: "Empty, separator and non-log lines are rejected with a reason instead of producing junk events." },
          ].map((f) => (
            <Card key={f.title} className="bg-card/50">
              <CardHeader className="p-4">
                <CardTitle className="text-sm">{f.title}</CardTitle>
                <CardDescription>{f.body}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}