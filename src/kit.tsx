import { createElement, type ComponentType, type ReactNode, type CSSProperties } from 'react';
import { getSdk } from '@ervisio/plugin-sdk';
function wrap<P>(name: string): ComponentType<P> {
  const C = (props: P) => createElement(getSdk().ui[name], props as Record<string, unknown>);
  C.displayName = name;
  return C as ComponentType<P>;
}
export const Button = wrap<{variant?:'primary'|'secondary'|'ghost'|'danger'|'danger-solid';icon?:string;size?:'sm'|'md'|'lg';loading?:boolean;disabled?:boolean;block?:boolean;onClick?():void;children?:ReactNode;title?:string}>('Button');
export const Icon = wrap<{name:string;size?:number;style?:CSSProperties}>('Icon');
export const Badge = wrap<{tone?:'ok'|'warn'|'err'|'info'|'neutral';children:ReactNode}>('Badge');
export const Select = wrap<{label?:ReactNode;hint?:ReactNode;value:string;onChange(v:string):void;options:Array<{label:string;value:string;disabled?:boolean}>;disabled?:boolean}>('Select');
export const Checkbox = wrap<{checked:boolean;onChange(v:boolean):void;label?:ReactNode;disabled?:boolean}>('Checkbox');
export const Tabs = wrap<{items:{id:string;label:string;icon?:string;count?:number}[];value:string;onChange(v:string):void}>('Tabs');
export const StatCard = wrap<{hue:'term'|'ov'|'sw'|'plg';icon:string;label:ReactNode;value:ReactNode;sub?:ReactNode}>('StatCard');
export const Skeleton = wrap<{lines?:number}>('Skeleton');
export const ConfirmDialog = wrap<{open:boolean;onClose():void;onConfirm():void;title:ReactNode;description?:ReactNode;confirmLabel:string;cancelLabel?:string;confirmText?:string;danger?:boolean;icon?:string}>('ConfirmDialog');
export const toast = {
  ok:(title:string,detail?:string)=>getSdk().ui.toast.ok(title,detail),
  err:(title:string,detail?:string)=>getSdk().ui.toast.err(title,detail),
  info:(title:string,detail?:string)=>getSdk().ui.toast.info(title,detail)
};
