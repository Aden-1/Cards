(function () {
  const root = document.documentElement;
  const appConfig = window.cardsConfig || {};
  const isAuthenticated = Boolean(appConfig.isAuthenticated);
  const userTheme = typeof appConfig.userTheme === 'string' ? appConfig.userTheme : '';
  let savedTheme = '';
  try {
    savedTheme = localStorage.getItem('cards-theme') || '';
  } catch (error) {
    // Some mobile privacy modes disable local storage. The page still works
    // with the default theme in that case.
  }
  const initialTheme = isAuthenticated
    ? (userTheme || 'dark')
    : (savedTheme || 'light');
  root.setAttribute('data-theme', initialTheme);

  function updateThemeButton(theme) {
    const btn = document.getElementById('themeToggle');
    if (!btn) {
      return;
    }
    btn.textContent = theme === 'dark' ? 'Light Mode' : 'Dark Mode';
    btn.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
  }

  async function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    if (isAuthenticated) {
      try {
        await fetch('/theme', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': window.cardsCsrfToken
          },
          body: JSON.stringify({ theme })
        });
      } catch (error) {
        // Keep the UI responsive even if saving the preference fails.
      }
    } else {
      try {
        localStorage.setItem('cards-theme', theme);
      } catch (error) {
        // The theme remains applied for this session if storage is unavailable.
      }
    }
    updateThemeButton(theme);
  }

  window.cardsApp = {
    showToast(message, level) {
      const toast = document.createElement('div');
      toast.className = `feedback-message ${level === 'error' ? 'error' : 'success'}`;
      toast.textContent = message;
      document.body.appendChild(toast);
      setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 260ms ease';
        setTimeout(() => toast.remove(), 260);
      }, 2400);
    }
  };

  document.addEventListener('DOMContentLoaded', function () {
    updateThemeButton(root.getAttribute('data-theme') || initialTheme);

    const siteHeader = document.querySelector('.site-header');
    const mobileHeaderQuery = window.matchMedia('(max-width: 820px)');
    let lastScrollY = window.scrollY;
    let headerHidden = false;

    function setHeaderHidden(hidden) {
      if (!siteHeader || headerHidden === hidden) {
        return;
      }
      siteHeader.classList.toggle('is-hidden-on-scroll', hidden);
      headerHidden = hidden;
    }

    function syncHeaderVisibility() {
      if (!siteHeader) {
        return;
      }
      if (!mobileHeaderQuery.matches) {
        setHeaderHidden(false);
        lastScrollY = window.scrollY;
        return;
      }

      const currentScrollY = window.scrollY;
      const scrollDelta = currentScrollY - lastScrollY;

      if (currentScrollY <= 24) {
        setHeaderHidden(false);
      } else if (scrollDelta > 8) {
        setHeaderHidden(true);
      } else if (scrollDelta < -8) {
        setHeaderHidden(false);
      }

      lastScrollY = currentScrollY;
    }

    syncHeaderVisibility();
    let scrollFramePending = false;
    function scheduleHeaderVisibility() {
      if (scrollFramePending) {
        return;
      }
      scrollFramePending = true;
      window.requestAnimationFrame(() => {
        scrollFramePending = false;
        syncHeaderVisibility();
      });
    }

    window.addEventListener('scroll', scheduleHeaderVisibility, { passive: true });
    if (typeof mobileHeaderQuery.addEventListener === 'function') {
      mobileHeaderQuery.addEventListener('change', syncHeaderVisibility);
    } else if (typeof mobileHeaderQuery.addListener === 'function') {
      // Safari versions that predate MediaQueryList.addEventListener.
      mobileHeaderQuery.addListener(syncHeaderVisibility);
    }

    const themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
      themeToggle.addEventListener('click', async function () {
        const current = root.getAttribute('data-theme') || 'light';
        await applyTheme(current === 'dark' ? 'light' : 'dark');
      });
    }

    document.querySelectorAll('form').forEach((form) => {
      if ((form.method || '').toLowerCase() !== 'post') {
        return;
      }
      if (!form.querySelector('input[name="csrf_token"]') && window.cardsCsrfToken) {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'csrf_token';
        input.value = window.cardsCsrfToken;
        form.appendChild(input);
      }

      form.addEventListener('submit', function () {
        const submitButtons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
        submitButtons.forEach((button) => {
          if (button.dataset.locked === 'true') {
            return;
          }
          button.dataset.locked = 'true';
          button.disabled = true;
          if (button.tagName === 'BUTTON') {
            button.dataset.originalText = button.textContent;
            button.textContent = button.dataset.loadingText || 'Saving...';
          }
          if (button.tagName === 'INPUT') {
            button.dataset.originalValue = button.value;
            button.value = button.dataset.loadingText || 'Saving...';
          }
        });
      });
    });

    document.querySelectorAll('[data-random-placeholder="search"]').forEach((input) => {
      const placeholderMessages = [
        'Search MCAT enzymes, SAT geometry, APUSH unit 8',
        'Find a deck for today',
        'Study smarter with public decks',
        'Find tags you need now',
        'Explore new decks in seconds',
        'Search by tag and learn',
        'Find the right deck fast'
      ];
      const randomIndex = Math.floor(Math.random() * placeholderMessages.length);
      input.placeholder = placeholderMessages[randomIndex];
    });

    const mobileNavToggle = document.getElementById('mobileNavToggle');
    const primaryNav = document.getElementById('primaryNav');
    if (mobileNavToggle && primaryNav) {
      function setMobileNavOpen(open) {
        mobileNavToggle.setAttribute('aria-expanded', String(open));
        mobileNavToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
        primaryNav.classList.toggle('is-open', open);
      }

      mobileNavToggle.addEventListener('click', function () {
        const isOpen = mobileNavToggle.getAttribute('aria-expanded') === 'true';
        setMobileNavOpen(!isOpen);
      });

      primaryNav.addEventListener('click', function (event) {
        if (event.target.closest('a')) {
          setMobileNavOpen(false);
        }
      });

      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && mobileNavToggle.getAttribute('aria-expanded') === 'true') {
          setMobileNavOpen(false);
          mobileNavToggle.focus();
        }
      });
    }

    document.querySelectorAll('[data-toggle-copy]').forEach((button) => {
      button.addEventListener('click', async function () {
        const target = document.getElementById(button.dataset.toggleCopy);
        if (!target) {
          return;
        }
        try {
          await navigator.clipboard.writeText(target.value || target.textContent || '');
          window.cardsApp.showToast('Copied to clipboard', 'success');
        } catch (error) {
          window.cardsApp.showToast('Could not copy right now', 'error');
        }
      });
    });

    const url = new URL(window.location.href);
    const notice = url.searchParams.get('notice');
    const level = url.searchParams.get('level') || 'success';
    if (notice) {
      window.cardsApp.showToast(notice, level);
      url.searchParams.delete('notice');
      url.searchParams.delete('level');
      window.history.replaceState({}, document.title, url.toString());
    }
  });
})();
