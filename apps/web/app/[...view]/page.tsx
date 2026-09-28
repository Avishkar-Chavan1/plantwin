import { ProcessTwinConsole } from "../../components/process-twin-console";

export default function ProductView({ params }: { params: { view: string[] } }) {
  return <ProcessTwinConsole initialView={params.view[0] ?? "dashboard"} />;
}
