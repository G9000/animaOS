import type { CoreFsOperationRequest } from "../src/types";

const exactSearchRequest: CoreFsOperationRequest = {
  operation: "search",
  query: "alpha",
  searchMode: "exact",
};

void exactSearchRequest;
