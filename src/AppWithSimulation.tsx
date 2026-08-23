import { useState } from 'react'
import AppCorrected from './AppCorrected'
import CapacityPlanning from './CapacityPlanning'
import PolicySimulation from './PolicySimulation'

export default function AppWithSimulation() {
  const [simulationOpen, setSimulationOpen] = useState(false)
  const [capacityOpen, setCapacityOpen] = useState(false)
  return (
    <>
      <AppCorrected />
      <button type="button" className="capacity-planning-launcher" onClick={() => { setSimulationOpen(false); setCapacityOpen(true) }} aria-haspopup="dialog" aria-expanded={capacityOpen}>
        <span>STEP 8–10</span>容量配分・未収容原因
      </button>
      <button type="button" className="simulation-launcher" onClick={() => { setCapacityOpen(false); setSimulationOpen(true) }} aria-haspopup="dialog" aria-expanded={simulationOpen}>
        <span>STEP 7</span>対策シミュレーション
      </button>
      <CapacityPlanning open={capacityOpen} onClose={() => setCapacityOpen(false)} />
      <PolicySimulation open={simulationOpen} onClose={() => setSimulationOpen(false)} />
    </>
  )
}
