import { ProcessTwinConsole } from "../../components/process-twin-console";

export default async function ProductView({ params }: { params: Promise<{ view: string[] }> }) {
  const resolvedParams = await params;
  return <ProcessTwinConsole initialView={resolvedParams.view[0] ?? "dashboard"} />;
}
