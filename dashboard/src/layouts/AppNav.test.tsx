import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppNav } from './AppNav';

describe('AppNav', () => {
  it('includes the Copilot workflow without marking it as forthcoming', () => {
    render(
      <MemoryRouter initialEntries={['/copilot']}>
        <AppNav />
      </MemoryRouter>,
    );

    const copilot = screen.getByRole('link', { name: 'Copilot' });

    expect(copilot).toHaveAttribute('href', '/copilot');
    expect(copilot).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText('soon')).not.toBeInTheDocument();
  });
});
