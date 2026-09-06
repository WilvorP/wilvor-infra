import { describe, expect, it } from 'vitest';

import { asCoordinate, asCountMap, asNumber, asStringArray } from './coerce';
import {
  formatAge,
  formatBoolean,
  formatCount,
  formatUtcTime,
  humaniseToken,
  NOT_REPORTED,
  secondsSince,
} from './format';

describe('coercion helpers', () => {
  it('treats non-finite numbers as absent', () => {
    expect(asNumber(42)).toBe(42);
    expect(asNumber(0)).toBe(0);
    expect(asNumber(Number.NaN)).toBeNull();
    expect(asNumber(Number.POSITIVE_INFINITY)).toBeNull();
    expect(asNumber('42')).toBeNull();
    expect(asNumber(undefined)).toBeNull();
  });

  it('discards non-numeric entries from a count map', () => {
    expect(asCountMap({ HIGH: 3, LOW: 'x', MED: null })).toEqual({ HIGH: 3 });
    expect(asCountMap(null)).toEqual({});
    expect(asCountMap([1, 2])).toEqual({});
  });

  it('keeps only non-empty strings in a string array', () => {
    expect(asStringArray(['a', '', '  ', 7, null, 'b'])).toEqual(['a', 'b']);
    expect(asStringArray(undefined)).toEqual([]);
  });

  it('rejects out-of-range and partial coordinates', () => {
    expect(asCoordinate(-122.4, 37.6)).toEqual([-122.4, 37.6]);
    // Aircraft without a fix carry no latitude/longitude at all.
    expect(asCoordinate(-122.4, undefined)).toBeNull();
    expect(asCoordinate(-181, 37.6)).toBeNull();
    expect(asCoordinate(-122.4, 91)).toBeNull();
  });
});

describe('formatting', () => {
  it('renders an explicit marker for absent values', () => {
    expect(formatCount(undefined)).toBe(NOT_REPORTED);
    expect(formatAge(null)).toBe(NOT_REPORTED);
    expect(formatUtcTime(undefined)).toBe(NOT_REPORTED);
    expect(humaniseToken(null)).toBe(NOT_REPORTED);
  });

  it('distinguishes zero from absent', () => {
    // Zero active hazards is an operational fact; absent is a data gap.
    expect(formatCount(0)).toBe('0');
    expect(formatCount(1234)).toBe('1,234');
  });

  it('formats ages across each magnitude', () => {
    expect(formatAge(0)).toBe('0s');
    expect(formatAge(45)).toBe('45s');
    expect(formatAge(260)).toBe('4m 20s');
    expect(formatAge(3_900)).toBe('1h 05m');
    expect(formatAge(90_000)).toBe('1d 01h');
    expect(formatAge(-5)).toBe(NOT_REPORTED);
  });

  it('renders timestamps in UTC regardless of the browser timezone', () => {
    // Operations, METAR, TAF and SIGMET validity windows are all UTC.
    expect(formatUtcTime('2026-09-03T14:32:07Z')).toBe('14:32:07Z');
    expect(formatUtcTime('2026-09-03T14:32:07+02:00')).toBe('12:32:07Z');
    expect(formatUtcTime('not a date')).toBe(NOT_REPORTED);
  });

  it('computes age from an ISO timestamp', () => {
    const now = Date.parse('2026-09-03T12:00:30Z');

    expect(secondsSince('2026-09-03T12:00:00Z', now)).toBe(30);
    // A future timestamp clamps to zero rather than reporting a negative age.
    expect(secondsSince('2026-09-03T12:01:00Z', now)).toBe(0);
    expect(secondsSince('nonsense', now)).toBeNull();
  });

  it('humanises enum tokens', () => {
    expect(humaniseToken('WEATHER_IMPACTED')).toBe('Weather impacted');
    expect(humaniseToken('EVALUATE_DIVERSION')).toBe('Evaluate diversion');
    expect(humaniseToken('EVALUATE_DIVERSION')).not.toBe('Divert');
  });

  it('renders booleans as Yes/No and never as zero', () => {
    expect(formatBoolean(true)).toBe('Yes');
    expect(formatBoolean(false)).toBe('No');
    expect(formatBoolean(undefined)).toBe(NOT_REPORTED);
    expect(formatBoolean(0)).toBe(NOT_REPORTED);
  });
});
