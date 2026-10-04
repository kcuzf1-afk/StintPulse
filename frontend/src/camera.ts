/** Camera selection for the live onboard (OBS Virtual Camera or any webcam). */

export interface CameraInfo {
  deviceId: string
  label: string
}

export const OBS_CAMERA = /obs[\s-]*virtual[\s-]*cam/i

export function videoInputs(devices: MediaDeviceInfo[]): CameraInfo[] {
  return devices
    .filter((d) => d.kind === 'videoinput')
    .map((d) => ({ deviceId: d.deviceId, label: d.label }))
}

/** Labels stay empty until the browser has granted camera access once. */
export const needsPermission = (cameras: CameraInfo[]) =>
  !cameras.length || cameras.every((c) => !c.label)

/** Saved label first, then OBS Virtual Camera, then the first camera. */
export function pickCamera(cameras: CameraInfo[], saved: string): CameraInfo | null {
  return (
    (saved && cameras.find((c) => c.label === saved)) ||
    cameras.find((c) => OBS_CAMERA.test(c.label)) ||
    cameras[0] ||
    null
  )
}

export function cameraError(error: unknown, de: boolean): string {
  const t = (a: string, b: string) => (de ? a : b)
  const name = (error as { name?: string })?.name || ''
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return t(
        'Kamerazugriff verweigert. In der Adressleiste die Kamera für diese Seite erlauben (nur localhost oder HTTPS).',
        'Camera access denied. Allow the camera for this page in the address bar (localhost or HTTPS only).',
      )
    case 'NotFoundError':
    case 'OverconstrainedError':
      return t(
        'Keine Kamera gefunden. In OBS „Virtuelle Kamera starten“ klicken und dann „Erneut verbinden“.',
        'No camera found. Click "Start Virtual Camera" in OBS, then "Reconnect".',
      )
    case 'NotReadableError':
    case 'AbortError':
      return t(
        'Kamera ist belegt oder liefert kein Bild. In OBS die virtuelle Kamera (neu) starten; andere Programme, die sie nutzen, schließen.',
        'Camera is busy or delivers no image. (Re)start the OBS virtual camera and close other apps using it.',
      )
    default:
      return t('Kamera konnte nicht geöffnet werden: ', 'Camera could not be opened: ') + String(error)
  }
}
