import { AppShell } from "@/components/app-shell"
import { TooltipProvider } from "@/components/ui/tooltip"

function App() {
  return (
    <TooltipProvider delayDuration={150}>
      <AppShell />
    </TooltipProvider>
  )
}

export default App
