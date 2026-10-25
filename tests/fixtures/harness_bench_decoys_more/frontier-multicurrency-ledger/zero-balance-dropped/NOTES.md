The reference with one change: `balance CSV ACCOUNT` leaves out a currency whose balance is
now zero. Breaks SPEC.md 7.7: "balance CSV ACCOUNT prints one line per currency of the
account, in code order" with 7.4: balances() has "one entry for every currency ever posted to
that account (also when its balance is now zero)".
