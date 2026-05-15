'use client';

import { useEffect, useState } from 'react';

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Check if access_token exists in cookies
    const token = document.cookie
      .split('; ')
      .find(row => row.startsWith('access_token='))
      ?.split('=')[1];
    
    setIsAuthenticated(!!token);
    setIsLoading(false);
  }, []);

  return { isAuthenticated, isLoading };
}
