import { useState } from 'react'
import AppCorrected from './AppCorrected'
import PolicySimulation from './PolicySimulation'

export default function AppWithSimulation() {
  const [simulationOpen, setSimulationOpen] = useState(false)

  return (
    <>
      <AppCorrected />
      <button
        type="button"
        className="simulation-launcher"
        onClick={() => setSimulationOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={simulationOpen}
      >
        <span>STEP 7</span>
        対策シミュレーション
      </button>
      <PolicySimulation open={simulationOpen} onClose={() => setSimulationOpen(false)} />
    </>
  )
}
