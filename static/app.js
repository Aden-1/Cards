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

  const mathSymbols = Object.freeze({
    alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', theta: 'θ', lambda: 'λ',
    mu: 'μ', pi: 'π', sigma: 'σ', phi: 'φ', omega: 'ω', Delta: 'Δ',
    Sigma: 'Σ', Omega: 'Ω', times: '×', cdot: '·', pm: '±', le: '≤',
    ge: '≥', ne: '≠', approx: '≈', infty: '∞', sum: '∑', int: '∫',
    rightarrow: '→', leftarrow: '←'
  });

  function groupedMath(source, start) {
    if (source[start] !== '{') {
      return { value: source[start] || '', next: Math.min(source.length, start + 1) };
    }
    let depth = 1;
    for (let index = start + 1; index < source.length; index += 1) {
      if (source[index] === '{') { depth += 1; }
      if (source[index] === '}') { depth -= 1; }
      if (depth === 0) {
        return { value: source.slice(start + 1, index), next: index + 1 };
      }
    }
    return { value: source.slice(start + 1), next: source.length };
  }

  function appendMath(parent, source) {
    let index = 0;
    while (index < source.length) {
      const character = source[index];
      if (character === '\\') {
        const commandMatch = source.slice(index + 1).match(/^[A-Za-z]+/);
        if (!commandMatch) {
          parent.append(document.createTextNode(source[index + 1] || '\\'));
          index += source[index + 1] ? 2 : 1;
          continue;
        }
        const command = commandMatch[0];
        index += command.length + 1;
        if (command === 'frac') {
          const numerator = groupedMath(source, index);
          const denominator = groupedMath(source, numerator.next);
          const fraction = document.createElement('span');
          fraction.className = 'math-fraction';
          const top = document.createElement('span');
          top.className = 'math-numerator';
          appendMath(top, numerator.value);
          const bottom = document.createElement('span');
          bottom.className = 'math-denominator';
          appendMath(bottom, denominator.value);
          fraction.append(top, bottom);
          parent.append(fraction);
          index = denominator.next;
          continue;
        }
        if (command === 'sqrt') {
          const radicand = groupedMath(source, index);
          const squareRoot = document.createElement('span');
          squareRoot.className = 'math-square-root';
          squareRoot.append(document.createTextNode('√'));
          const value = document.createElement('span');
          value.className = 'math-radicand';
          appendMath(value, radicand.value);
          squareRoot.append(value);
          parent.append(squareRoot);
          index = radicand.next;
          continue;
        }
        parent.append(document.createTextNode(mathSymbols[command] || command));
        continue;
      }
      if (character === '^' || character === '_') {
        const value = groupedMath(source, index + 1);
        const script = document.createElement(character === '^' ? 'sup' : 'sub');
        appendMath(script, value.value);
        parent.append(script);
        index = value.next;
        continue;
      }
      if (character === '{' || character === '}') {
        index += 1;
        continue;
      }
      parent.append(document.createTextNode(character));
      index += 1;
    }
  }

  function richTextFragment(value) {
    const source = String(value ?? '');
    const fragment = document.createDocumentFragment();
    let index = 0;
    while (index < source.length) {
      const markers = [
        { token: '$$', tag: 'math', display: true },
        { token: '**', tag: 'strong' },
        { token: '`', tag: 'code' },
        { token: '$', tag: 'math', display: false },
        { token: '*', tag: 'em' }
      ];
      const marker = markers.find((candidate) => source.startsWith(candidate.token, index));
      if (marker) {
        const end = source.indexOf(marker.token, index + marker.token.length);
        if (end > index + marker.token.length) {
          const content = source.slice(index + marker.token.length, end);
          const element = document.createElement(marker.tag === 'math' ? 'span' : marker.tag);
          if (marker.tag === 'math') {
            element.className = `math-expression${marker.display ? ' display' : ''}`;
            element.setAttribute('role', 'math');
            element.setAttribute('aria-label', content);
            appendMath(element, content);
          } else {
            element.textContent = content;
          }
          fragment.append(element);
          index = end + marker.token.length;
          continue;
        }
      }
      if (source[index] === '\n') {
        fragment.append(document.createElement('br'));
      } else {
        fragment.append(document.createTextNode(source[index]));
      }
      index += 1;
    }
    return fragment;
  }

  function renderRichText(element, value) {
    if (!element) { return; }
    element.replaceChildren(richTextFragment(value));
    element.classList.add('rich-text');
  }

  function enhanceRichText(container = document) {
    container.querySelectorAll('[data-rich-text]').forEach((element) => {
      if (element.dataset.richRendered === 'true') { return; }
      const value = element.textContent;
      renderRichText(element, value);
      element.dataset.richRendered = 'true';
    });
  }

  window.cardsApp = {
    enhanceRichText,
    renderRichText,
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
    enhanceRichText();

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
        'Search decks, quizzes, and topics',
        'What do you want to learn today?',
        'Find your next study set',
        'Search the learning library',
        'Explore decks and quizzes',
        'Learn something new',
        'Search subjects, skills, or topics',
        'Find cards for any subject',
        'Discover your next challenge',
        'Start with a topic you love',
        'Search public study sets',
        'Find a deck to master',
        'Explore ideas worth learning',
        'What are you studying?',
        'Search for a skill or subject',
        'Find your next review session',
        'Browse decks made for learning',
        'Search knowledge, one card at a time',
        'Pick a topic and start learning',
        'Find a better way to study',
        'Stack the deck in your favor',
        'Study smarter with public decks',
        'Find tags you need now',
        'Explore new decks in seconds',
        'Find the right deck fast',
        'Pick a deck, then play it smart'
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
