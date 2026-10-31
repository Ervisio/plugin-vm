import '@ervisio/plugin-sdk';

declare module '@ervisio/plugin-sdk' {
  interface PluginSDK {
    /** Host platform supplied by Ervisio 0.6.2; absent on older runtimes. */
    platform?: 'linux' | 'windows';
  }
}
