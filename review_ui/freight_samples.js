export const SAMPLE_LOADS = [
  {
    "load_id": "L-100481",
    "scenario": "Clean match -- invoice equals rate con, POD present, short wait. Auto-approves.",
    "rate_confirmation": {
      "load_id": "L-100481",
      "broker_name": "Cardinal Logistics Brokerage",
      "carrier_name": "Sunbelt Carriers LLC",
      "origin": "Dallas, TX",
      "destination": "Memphis, TN",
      "agreed_total": "$1,850.00",
      "pickup_date": "2026-06-08",
      "free_time_hours": 2,
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,600.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$250.00"
        }
      ],
      "approved_accessorials": {
        "detention": "$75.00"
      }
    },
    "invoice": {
      "invoice_number": "SB-44021",
      "load_id": "L-100481",
      "carrier_name": "Sunbelt Carriers LLC",
      "billed_total": "$1,850.00",
      "invoice_date": "2026-06-10",
      "line_items": [
        {
          "description": "Line Haul",
          "amount": "$1,600.00"
        },
        {
          "description": "FSC",
          "amount": "$250.00"
        }
      ]
    },
    "pod": {
      "load_id": "L-100481",
      "delivered": true,
      "arrival_time": "2026-06-09 08:00",
      "departure_time": "2026-06-09 09:30",
      "signed_by": "R. Alvarez"
    }
  },
  {
    "load_id": "L-100482",
    "scenario": "Broker OVERPAYS: linehaul billed above agreed AND fuel charged twice (duplicate).",
    "rate_confirmation": {
      "load_id": "L-100482",
      "broker_name": "Cardinal Logistics Brokerage",
      "carrier_name": "Ironwood Freight Inc",
      "origin": "Atlanta, GA",
      "destination": "Tampa, FL",
      "agreed_total": "$2,100.00",
      "pickup_date": "2026-06-09",
      "free_time_hours": 2,
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,800.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$300.00"
        }
      ],
      "approved_accessorials": {}
    },
    "invoice": {
      "invoice_number": "IW-7781",
      "load_id": "100482",
      "carrier_name": "Ironwood Freight Inc",
      "billed_total": "$2,500.00",
      "invoice_date": "2026-06-11",
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,900.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$300.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$300.00"
        }
      ]
    },
    "pod": {
      "load_id": "L-100482",
      "delivered": true,
      "arrival_time": "2026-06-10 13:00",
      "departure_time": "2026-06-10 14:15",
      "signed_by": "T. Nguyen"
    }
  },
  {
    "load_id": "L-100483",
    "scenario": "Unauthorized accessorial: carrier adds a liftgate fee never agreed on the rate con.",
    "rate_confirmation": {
      "load_id": "L-100483",
      "broker_name": "Cardinal Logistics Brokerage",
      "carrier_name": "Blue Ridge Transport",
      "origin": "Charlotte, NC",
      "destination": "Nashville, TN",
      "agreed_total": "$1,400.00",
      "pickup_date": "2026-06-10",
      "free_time_hours": 2,
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,150.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$250.00"
        }
      ],
      "approved_accessorials": {
        "detention": "$60.00"
      }
    },
    "invoice": {
      "invoice_number": "BR-3390",
      "load_id": "L-100483",
      "carrier_name": "Blue Ridge Transport",
      "billed_total": "$1,525.00",
      "invoice_date": "2026-06-12",
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,150.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$250.00"
        },
        {
          "description": "Liftgate Service",
          "amount": "$125.00"
        }
      ]
    },
    "pod": {
      "load_id": "L-100483",
      "delivered": true,
      "arrival_time": "2026-06-11 10:00",
      "departure_time": "2026-06-11 11:30",
      "signed_by": "M. Carter"
    }
  },
  {
    "load_id": "L-100484",
    "scenario": "Detention BILLED but POD has no arrival/departure timestamps -- deniable as-is. BLOCK for review.",
    "rate_confirmation": {
      "load_id": "L-100484",
      "broker_name": "Cardinal Logistics Brokerage",
      "carrier_name": "Sunbelt Carriers LLC",
      "origin": "Houston, TX",
      "destination": "New Orleans, LA",
      "agreed_total": "$1,250.00",
      "pickup_date": "2026-06-11",
      "free_time_hours": 2,
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,050.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$200.00"
        }
      ],
      "approved_accessorials": {
        "detention": "$75.00"
      }
    },
    "invoice": {
      "invoice_number": "SB-44090",
      "load_id": "L-100484",
      "carrier_name": "Sunbelt Carriers LLC",
      "billed_total": "$1,475.00",
      "invoice_date": "2026-06-13",
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,050.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$200.00"
        },
        {
          "description": "Detention (3 hrs)",
          "amount": "$225.00",
          "quantity": 3,
          "rate": "$75.00"
        }
      ]
    },
    "pod": {
      "load_id": "L-100484",
      "delivered": true,
      "arrival_time": null,
      "departure_time": null,
      "signed_by": "J. Pierre"
    }
  },
  {
    "load_id": "L-100485",
    "scenario": "THE MONEY CASE: POD proves a 4.25h wait (2.25h past free time) but carrier billed NO detention. Recoverable revenue they're leaving on the table.",
    "rate_confirmation": {
      "load_id": "L-100485",
      "broker_name": "Cardinal Logistics Brokerage",
      "carrier_name": "Ironwood Freight Inc",
      "origin": "Phoenix, AZ",
      "destination": "El Paso, TX",
      "agreed_total": "$1,700.00",
      "pickup_date": "2026-06-12",
      "free_time_hours": 2,
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,450.00",
          "rate": null
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$250.00"
        },
        {
          "description": "Detention",
          "amount": "$0.00",
          "rate": "$80.00"
        }
      ],
      "approved_accessorials": {
        "detention": "$80.00"
      }
    },
    "invoice": {
      "invoice_number": "IW-7820",
      "load_id": "L-100485",
      "carrier_name": "Ironwood Freight Inc",
      "billed_total": "$1,700.00",
      "invoice_date": "2026-06-14",
      "line_items": [
        {
          "description": "Linehaul",
          "amount": "$1,450.00"
        },
        {
          "description": "Fuel Surcharge",
          "amount": "$250.00"
        }
      ]
    },
    "pod": {
      "load_id": "L-100485",
      "delivered": true,
      "arrival_time": "2026-06-13 06:00",
      "departure_time": "2026-06-13 10:15",
      "signed_by": "D. Salazar"
    }
  }
];
export const CLIENT_PROFILE = {
  "name": "Acme Brokerage",
  "tolerance": 1,
  "default_detention_rate": 80,
  "free_time_hours": 2,
  "max_detention_hours": 6,
  "fuel_rule": {
    "kind": "pct_of_linehaul",
    "pct": 0.25,
    "tolerance": 5
  },
  "global_accessorial_caps": {
    "lumper": 150,
    "liftgate": 75
  },
  "disallowed_accessorials": [
    "tonu"
  ],
  "vocab_overlay_path": "engine/vocab/client_acme.example.json",
  "_comment": "Onboarding Acme = this file. Fuel must be ~25% of linehaul (±$5); lumper capped $150; liftgate capped $75; TONU not allowed; detention $80/hr, 6h max."
};
