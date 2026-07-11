import { toast as _toast, type ToastOptions } from 'react-toastify';

const defaults: ToastOptions = {
  position:        'top-right',
  autoClose:       5000,
  hideProgressBar: false,
  closeOnClick:    true,
  pauseOnHover:    true,
  draggable:       false,
};

export const toast = {
  error:   (msg: string, opts?: ToastOptions) => _toast.error(msg,   { ...defaults, ...opts }),
  success: (msg: string, opts?: ToastOptions) => _toast.success(msg, { ...defaults, ...opts }),
  info:    (msg: string, opts?: ToastOptions) => _toast.info(msg,    { ...defaults, ...opts }),
  warn:    (msg: string, opts?: ToastOptions) => _toast.warn(msg,    { ...defaults, ...opts }),
};
