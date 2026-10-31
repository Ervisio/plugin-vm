declare module '@novnc/novnc' {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, urlOrChannel: string | WebSocket, options?: { shared?: boolean });
    scaleViewport: boolean;
    resizeSession: boolean;
    qualityLevel: number;
    compressionLevel: number;
    showDotCursor: boolean;
    viewOnly: boolean;
    clipboardPasteFrom(text: string): void;
    sendCtrlAltDel(): void;
    disconnect(): void;
  }
}
