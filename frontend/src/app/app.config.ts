import { ApplicationConfig, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withViewTransitions } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { providePrimeNG } from 'primeng/config';
import Aura from '@primeuix/themes/aura';
import { MessageService } from 'primeng/api';

import { routes } from './app.routes';
import { errorLoggingInterceptor } from './interceptors/error-logging.interceptor';
import { AppearanceService } from './services/appearance.service';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideHttpClient(
      withInterceptors([errorLoggingInterceptor])
    ),
    provideRouter(routes, withViewTransitions()),
    provideAnimationsAsync(),
    MessageService,
    provideAppInitializer(() => inject(AppearanceService).initialize()),
    providePrimeNG({
      theme: {
        preset: Aura,
        options: {
          darkModeSelector: '.app-dark-mode'
        }
      }
    })
  ]
};
