import { useEffect, useState } from 'react'

import { loadDriveWriteStatus, type DriveWriteStatus } from '../api/drive'

const LOCAL: DriveWriteStatus = { scx: false, writesAvailable: true, writesEnabled: false }

/** Drive write availability for disabling upload/Final actions (local mode: writes available). */
export function useDriveWriteStatus(): DriveWriteStatus {
  const [status, setStatus] = useState<DriveWriteStatus>(LOCAL)
  useEffect(() => {
    let active = true
    void loadDriveWriteStatus().then((value) => { if (active) setStatus(value) })
    return () => { active = false }
  }, [])
  return status
}
